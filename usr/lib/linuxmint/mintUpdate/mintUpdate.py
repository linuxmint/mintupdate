#!/usr/bin/env python

try:
    import os
    import commands
    import sys
    import string
    import gtk
    import gtk.glade
    import gobject
    import tempfile
    import threading
    import time
    import gettext
    import fnmatch
    import urllib2
    from user import home
    sys.path.append('/usr/lib/linuxmint/common')
    from configobj import ConfigObj
except Exception, detail:
    print detail
    pass

try:
    import pygtk
    pygtk.require("2.0")
except Exception, detail:
    print detail
    pass

from subprocess import Popen, PIPE

try:
    numMintUpdate = commands.getoutput("ps -A | grep mintUpdate | wc -l")
    if (numMintUpdate != "0"):
        if (os.getuid() == 0):
            os.system("killall mintUpdate")
        else:
            print "Another mintUpdate is already running, exiting."
            sys.exit(1)
except Exception, detail:
    print detail

architecture = commands.getoutput("uname -a")
if (architecture.find("x86_64") >= 0):
    import ctypes
    libc = ctypes.CDLL('libc.so.6')
    libc.prctl(15, 'mintUpdate', 0, 0, 0)
else:
    import dl
    libc = dl.open('/lib/libc.so.6')
    libc.call('prctl', 15, 'mintUpdate', 0, 0, 0)

# i18n
gettext.install("mintupdate", "/usr/share/linuxmint/locale")

# i18n for menu item
menuName = _("Update Manager")
menuGenericName = _("Software Updates")
menuComment = _("Show and install available updates")


class ChangelogRetriever(threading.Thread):
    def __init__(self, source_package, level, version, wTree):
        threading.Thread.__init__(self)
        self.source_package = source_package
        self.level = level 
        self.version = version
        self.wTree = wTree
           
    def run(self):         
        gtk.gdk.threads_enter()
        self.wTree.get_widget("textview_changes").get_buffer().set_text(_("Downloading changelog..."))  
        gtk.gdk.threads_leave()       
                    
        changelog = ""        
        if (self.level == 1) or ("mint" in self.version) or ("mint" in self.source_package):                        
            #Get the mint change file for amd64              
            try:                      
                url = urllib2.urlopen("http://packages.linuxmint.com/dev/" + self.source_package + "_" + self.version + "_amd64.changes", None, 30)
                source = url.read()
                url.close()
                changes = source.split("\n")
                for change in changes:
                    change = change.strip()
                    if change.startswith("*"):
                        changelog = changelog + change + "\n"
            except:
                try:
                    url = urllib2.urlopen("http://packages.linuxmint.com/dev/" + self.source_package + "_" + self.version + "_i386.changes", None, 30)
                    source = url.read()
                    url.close()
                    changes = source.split("\n")
                    for change in changes:
                        change = change.strip()
                        if change.startswith("*"):
                            changelog = changelog + change + "\n"
                except:
                    changelog = _("No changelog available")                
        else:            
            try:
                source = commands.getoutput("aptitude changelog " + self.source_package)                    
                changes = source.split("urgency=")[1].split("\n")
                for change in changes:
                    change = change.strip()
                    if change.startswith("*"):
                        changelog = changelog + change + "\n"
            except Exception, detail:
                print detail
                changelog = _("No changelog available") + "\n" + _("Click on Edit->Software Sources and tick the 'Source code' option to enable access to the changelogs")
                
        gtk.gdk.threads_enter()                
        self.wTree.get_widget("textview_changes").get_buffer().set_text(changelog)        
        gtk.gdk.threads_leave()

class AutomaticRefreshThread(threading.Thread):
    def __init__(self, treeView, statusIcon, wTree):
        threading.Thread.__init__(self)
        self.treeView = treeView
        self.statusIcon = statusIcon
        self.wTree = wTree

    def run(self):
        global app_hidden
        global log
        try:
            while(True):
                prefs = read_configuration()
                timer = (prefs["timer_minutes"] * 60) + (prefs["timer_hours"] * 60 * 60) + (prefs["timer_days"] * 24 * 60 * 60)

                try:
                    log.writelines("++ Auto-refresh timer is going to sleep for " + str(prefs["timer_minutes"]) + " minutes, " + str(prefs["timer_hours"]) + " hours and " + str(prefs["timer_days"]) + " days\n")
                    log.flush()
                except:
                    pass # cause it might be closed already
                timetosleep = int(timer)
                if (timetosleep == 0):
                    time.sleep(60) # sleep 1 minute, don't mind the config we don't want an infinite loop to go nuts :)
                else:
                    time.sleep(timetosleep)
                    if (app_hidden == True):
                        try:
                            log.writelines("++ MintUpdate is in tray mode, performing auto-refresh\n")
                            log.flush()
                        except:
                            pass # cause it might be closed already
                        # Refresh
                        refresh = RefreshThread(self.treeView, self.statusIcon, self.wTree)
                        refresh.start()
                    else:
                        try:
                            log.writelines("++ The mintUpdate window is open, skipping auto-refresh\n")
                            log.flush()
                        except:
                            pass # cause it might be closed already

        except Exception, detail:
            try:
                log.writelines("-- Exception occured in the auto-refresh thread.. so it's probably dead now: " + str(detail) + "\n")
                log.flush()
            except:
                pass # cause it might be closed already

class InstallThread(threading.Thread):
    global icon_busy
    global icon_up2date
    global icon_updates
    global icon_error
    global icon_unknown
    global icon_apply

    def __init__(self, treeView, statusIcon, wTree):
        threading.Thread.__init__(self)
        self.treeView = treeView
        self.statusIcon = statusIcon
        self.wTree = wTree

    def run(self):
        global log
        try:
            log.writelines("++ Install requested by user\n")
            log.flush()
            gtk.gdk.threads_enter()
            self.wTree.get_widget("window1").window.set_cursor(gtk.gdk.Cursor(gtk.gdk.WATCH))
            self.wTree.get_widget("window1").set_sensitive(False)
            installNeeded = False
            packages = []
            model = self.treeView.get_model()
            gtk.gdk.threads_leave()

            iter = model.get_iter_first()
            history = open("/var/log/mintUpdate.history", "a")
            while (iter != None):
                checked = model.get_value(iter, 0)
                if (checked == "true"):
                    installNeeded = True
                    package = model.get_value(iter, 1)
                    level = model.get_value(iter, 7)
                    oldVersion = model.get_value(iter, 3)
                    newVersion = model.get_value(iter, 4)
                    history.write(commands.getoutput('date +"%d %b %Y %H:%M:%S"') + "\t" + package + "\t" + level + "\t" + oldVersion + "\t" + newVersion + "\n")
                    packages.append(package)
                    log.writelines("++ Will install " + str(package) + "\n")
                    log.flush()
                iter = model.iter_next(iter)
            history.close()

            if (installNeeded == True):
                gtk.gdk.threads_enter()
                self.statusIcon.set_from_file(icon_apply)
                self.statusIcon.set_tooltip(_("Installing updates"))
                gtk.gdk.threads_leave()

                log.writelines("++ Ready to launch synaptic\n")
                log.flush()
                cmd = ["sudo", "/usr/sbin/synaptic", "--hide-main-window",  \
                        "--non-interactive"]
                cmd.append("--progress-str")
                cmd.append("\"" + _("Please wait, this can take some time") + "\"")
                cmd.append("--finish-str")
                cmd.append("\"" + _("Update is complete") + "\"")
                f = tempfile.NamedTemporaryFile()

                for pkg in packages:
                    f.write("%s\tinstall\n" % pkg)
                cmd.append("--set-selections-file")
                cmd.append("%s" % f.name)
                f.flush()
                comnd = Popen(' '.join(cmd), stdout=log, stderr=log, shell=True)
                returnCode = comnd.wait()
                log.writelines("++ Return code:" + str(returnCode) + "\n")
                #sts = os.waitpid(comnd.pid, 0)
                f.close()
                log.writelines("++ Install finished\n")
                log.flush()

                gtk.gdk.threads_enter()
                self.statusIcon.set_from_file(icon_busy)
                self.statusIcon.set_tooltip(_("Checking for updates"))
                self.wTree.get_widget("window1").window.set_cursor(None)
                self.wTree.get_widget("window1").set_sensitive(True)
                global app_hidden
                app_hidden = True
                self.wTree.get_widget("window1").hide()
                gtk.gdk.threads_leave()

                # Refresh
                refresh = RefreshThread(self.treeView, self.statusIcon, self.wTree)
                refresh.start()
            else:
                # Stop the blinking but don't refresh
                gtk.gdk.threads_enter()
                self.wTree.get_widget("window1").window.set_cursor(None)
                self.wTree.get_widget("window1").set_sensitive(True)
                gtk.gdk.threads_leave()

        except Exception, detail:
            log.writelines("-- Exception occured in the install thread: " + str(detail) + "\n")
            log.flush()
            gtk.gdk.threads_enter()
            self.statusIcon.set_from_file(icon_error)
            self.statusIcon.set_tooltip(_("Could not install the security updates"))
            log.writelines("-- Could not install security updates\n")
            log.flush()
            #self.statusIcon.set_blinking(False)
            self.wTree.get_widget("window1").window.set_cursor(None)
            self.wTree.get_widget("window1").set_sensitive(True)
            gtk.gdk.threads_leave()

class RefreshThread(threading.Thread):
    global icon_busy
    global icon_up2date
    global icon_updates
    global icon_error
    global statusbar
    global context_id

    def __init__(self, treeview_update, statusIcon, wTree):
        threading.Thread.__init__(self)
        self.treeview_update = treeview_update
        self.statusIcon = statusIcon
        self.wTree = wTree

    def run(self):
        global log
        global app_hidden
        gtk.gdk.threads_enter()
        vpaned_position = wTree.get_widget("vpaned1").get_position()
        gtk.gdk.threads_leave()
        try:
            log.writelines("++ Starting refresh\n")
            log.flush()
            gtk.gdk.threads_enter()
            statusbar.push(context_id, _("Starting refresh..."))
            self.wTree.get_widget("window1").window.set_cursor(gtk.gdk.Cursor(gtk.gdk.WATCH))
            self.wTree.get_widget("window1").set_sensitive(False)
            self.wTree.get_widget("label_error_detail").set_text("")
            self.wTree.get_widget("hbox_error").hide()
            self.wTree.get_widget("scrolledwindow1").hide()
            self.wTree.get_widget("viewport1").hide()
            self.wTree.get_widget("label_error_detail").hide()
            self.wTree.get_widget("image_error").hide()
            # Starts the blinking
            self.statusIcon.set_from_file(icon_busy)
            self.statusIcon.set_tooltip(_("Checking for updates"))
            wTree.get_widget("vpaned1").set_position(vpaned_position)
            #self.statusIcon.set_blinking(True)
            gtk.gdk.threads_leave()

            model = gtk.TreeStore(str, str, gtk.gdk.Pixbuf, str, str, str, str, str, object, int, str, str) # (check, packageName, level, oldVersion, newVersion, warning, extrainfo, stringLevel, description, size, stringSize, sourcePackage)
            model.set_sort_column_id( 7, gtk.SORT_ASCENDING )         

            prefs = read_configuration()
            
            # Check to see if no other APT process is running
            p1 = Popen(['ps', '-U', 'root', '-o', 'comm'], stdout=PIPE)
            p = p1.communicate()[0]
            running = False
            pslist = p.split('\n')
            for process in pslist:
                if process.strip() in ["dpkg", "apt-get","synaptic","update-manager", "adept", "adept-notifier"]:
                    running = True
                    break
            if (running == True):
                gtk.gdk.threads_enter()
                self.statusIcon.set_from_file(icon_unknown)
                self.statusIcon.set_tooltip(_("Another application is using APT"))
                statusbar.push(context_id, _("Another application is using APT"))
                log.writelines("-- Another application is using APT\n")
                log.flush()
                #self.statusIcon.set_blinking(False)
                self.wTree.get_widget("window1").window.set_cursor(None)
                self.wTree.get_widget("window1").set_sensitive(True)
                gtk.gdk.threads_leave()
                return False

            gtk.gdk.threads_enter()
            statusbar.push(context_id, _("Finding the list of updates..."))
            wTree.get_widget("vpaned1").set_position(vpaned_position)
            gtk.gdk.threads_leave()
            if app_hidden:
                updates = commands.getoutput("/usr/lib/linuxmint/mintUpdate/checkAPT.py | grep \"###\"")
            else:
                updates = commands.getoutput("/usr/lib/linuxmint/mintUpdate/checkAPT.py --use-synaptic | grep \"###\"")
           
            # Look for mintupdate
            if ("UPDATE###mintupdate###" in updates):                
                new_mintupdate = True
            else:
                new_mintupdate = False
           
            updates = string.split(updates, "\n")                            
                
            # Look at the packages one by one
            list_of_packages = ""
            num_visible = 0
            num_safe = 0            
            download_size = 0
            num_ignored = 0
            ignored_list = []
            if os.path.exists("/etc/linuxmint/mintupdate.ignored"):
                blacklist_file = open("/etc/linuxmint/mintupdate.ignored", "r")
                for blacklist_line in blacklist_file:
                    ignored_list.append(blacklist_line.strip())
                blacklist_file.close()                

            if (len(updates) == None):
                self.statusIcon.set_from_file(icon_up2date)
                self.statusIcon.set_tooltip(_("Your system is up to date"))
                statusbar.push(context_id, _("Your system is up to date"))
                log.writelines("++ System is up to date\n")
                log.flush()
            else:
                for pkg in updates:
                    values = string.split(pkg, "###")
                    if len(values) == 7:
                        status = values[0]
                        if (status == "ERROR"):
                            error_msg = commands.getoutput("/usr/lib/linuxmint/mintUpdate/checkAPT.py | grep -v \"ERROR###\"")
                            gtk.gdk.threads_enter()
                            self.statusIcon.set_from_file(icon_error)
                            self.statusIcon.set_tooltip(_("Could not refresh the list of packages"))
                            statusbar.push(context_id, _("Could not refresh the list of packages"))
                            log.writelines("-- Error in checkAPT.py, could not refresh the list of packages\n")
                            log.flush()
                            self.wTree.get_widget("label_error_detail").set_text(error_msg)
                            self.wTree.get_widget("label_error_detail").show()
                            self.wTree.get_widget("viewport1").show()
                            self.wTree.get_widget("scrolledwindow1").show()
                            self.wTree.get_widget("image_error").show()
                            self.wTree.get_widget("hbox_error").show()
                            #self.statusIcon.set_blinking(False)
                            self.wTree.get_widget("window1").window.set_cursor(None)
                            self.wTree.get_widget("window1").set_sensitive(True)
                            #statusbar.push(context_id, _(""))
                            gtk.gdk.threads_leave()
                            return False
                        package = values[1]
                        packageIsBlacklisted = False
                        for blacklist in ignored_list:
                            if fnmatch.fnmatch(package, blacklist):
                                num_ignored = num_ignored + 1
                                packageIsBlacklisted = True
                                break

                        if packageIsBlacklisted:
                            continue

                        newVersion = values[2]
                        oldVersion = values[3]
                        size = int(values[4])
                        source_package = values[5]
                        description = values[6]

                        strSize = size_to_string(size)

                        level = 3 # Level 3 by default
                        extraInfo = ""
                        warning = ""
                        rulesFile = open("/usr/lib/linuxmint/mintUpdate/rules","r")
                        rules = rulesFile.readlines()
                        goOn = True
                        foundPackageRule = False # whether we found a rule with the exact package name or not
                        for rule in rules:
                            if (goOn == True):
                                rule_fields = rule.split("|")
                                if (len(rule_fields) == 5):
                                    rule_package = rule_fields[0]
                                    rule_version = rule_fields[1]
                                    rule_level = rule_fields[2]
                                    rule_extraInfo = rule_fields[3]
                                    rule_warning = rule_fields[4]
                                    if (rule_package == package):
                                        foundPackageRule = True
                                        if (rule_version == newVersion):
                                            level = rule_level
                                            extraInfo = rule_extraInfo
                                            warning = rule_warning
                                            goOn = False # We found a rule with the exact package name and version, no need to look elsewhere
                                        else:
                                            if (rule_version == "*"):
                                                level = rule_level
                                                extraInfo = rule_extraInfo
                                                warning = rule_warning
                                    else:
                                        if (rule_package.startswith("*")):
                                            keyword = rule_package.replace("*", "")
                                            index = package.find(keyword)
                                            if (index > -1 and foundPackageRule == False):
                                                level = rule_level
                                                extraInfo = rule_extraInfo
                                                warning = rule_warning
                        rulesFile.close()

                        level = int(level)
                        if (prefs["level" + str(level) + "_visible"]):                            
                            if (new_mintupdate):
                                if (package == "mintupdate"):
                                    list_of_packages = list_of_packages + " " + package
                                    iter = model.insert_before(None, None)
                                    model.set_value(iter, 0, "true")
                                    model.row_changed(model.get_path(iter), iter)
                                    model.set_value(iter, 1, package)
                                    model.set_value(iter, 2, gtk.gdk.pixbuf_new_from_file("/usr/lib/linuxmint/mintUpdate/icons/level" + str(level) + ".png"))
                                    model.set_value(iter, 3, oldVersion)
                                    model.set_value(iter, 4, newVersion)
                                    model.set_value(iter, 5, warning)
                                    model.set_value(iter, 6, extraInfo)
                                    model.set_value(iter, 7, str(level))
                                    model.set_value(iter, 8, description)
                                    model.set_value(iter, 9, size)
                                    model.set_value(iter, 10, strSize)
                                    model.set_value(iter, 11, source_package)                            
                                    num_visible = num_visible + 1
                                                                                                   
                                #else:
                                #    model.set_value(iter, 0, "false")                                    
                            else:
                                list_of_packages = list_of_packages + " " + package
                                iter = model.insert_before(None, None)
                                if (prefs["level" + str(level) + "_safe"]):
                                    model.set_value(iter, 0, "true")                            
                                    num_safe = num_safe + 1
                                    download_size = download_size + size
                                else:
                                    model.set_value(iter, 0, "false") 
                                                                                  
                                model.row_changed(model.get_path(iter), iter)
                                model.set_value(iter, 1, package)
                                model.set_value(iter, 2, gtk.gdk.pixbuf_new_from_file("/usr/lib/linuxmint/mintUpdate/icons/level" + str(level) + ".png"))
                                model.set_value(iter, 3, oldVersion)
                                model.set_value(iter, 4, newVersion)
                                model.set_value(iter, 5, warning)
                                model.set_value(iter, 6, extraInfo)
                                model.set_value(iter, 7, str(level))
                                model.set_value(iter, 8, description)
                                model.set_value(iter, 9, size)
                                model.set_value(iter, 10, strSize)
                                model.set_value(iter, 11, source_package)                            
                                num_visible = num_visible + 1

                gtk.gdk.threads_enter()  
                if (new_mintupdate):
                    self.statusString = _("A new version of the update manager is available")
                    self.statusIcon.set_from_file(icon_updates)
                    self.statusIcon.set_tooltip(self.statusString)
                    statusbar.push(context_id, self.statusString)
                    log.writelines("++ Found a new version of mintupdate\n")
                    log.flush()
                else:
                    if (num_safe > 0):
                        if (num_safe == 1):
                            if (num_ignored == 0):
                                self.statusString = _("1 recommended update available (%(size)s)") % {'size':size_to_string(download_size)}
                            elif (num_ignored == 1):
                                self.statusString = _("1 recommended update available (%(size)s), 1 ignored") % {'size':size_to_string(download_size)}
                            elif (num_ignored > 1):
                                self.statusString = _("1 recommended update available (%(size)s), %(ignored)d ignored") % {'size':size_to_string(download_size), 'ignored':num_ignored}
                        else:
                            if (num_ignored == 0):
                                self.statusString = _("%(recommended)d recommended updates available (%(size)s)") % {'recommended':num_safe, 'size':size_to_string(download_size)}
                            elif (num_ignored == 1):
                                self.statusString = _("%(recommended)d recommended updates available (%(size)s), 1 ignored") % {'recommended':num_safe, 'size':size_to_string(download_size)}                            
                            elif (num_ignored > 0):
                                self.statusString = _("%(recommended)d recommended updates available (%(size)s), %(ignored)d ignored") % {'recommended':num_safe, 'size':size_to_string(download_size), 'ignored':num_ignored}
                        self.statusIcon.set_from_file(icon_updates)
                        self.statusIcon.set_tooltip(self.statusString)
                        statusbar.push(context_id, self.statusString)
                        log.writelines("++ Found " + str(num_safe) + " recommended software updates\n")
                        log.flush()
                    else:
                        self.statusIcon.set_from_file(icon_up2date)
                        self.statusIcon.set_tooltip(_("Your system is up to date"))
                        statusbar.push(context_id, _("Your system is up to date"))
                        log.writelines("++ System is up to date\n")
                        log.flush()

            log.writelines("++ Refresh finished\n")
            log.flush()
            # Stop the blinking
            #self.statusIcon.set_blinking(False)
            self.wTree.get_widget("notebook_details").set_current_page(0)
            self.wTree.get_widget("window1").window.set_cursor(None)
            self.treeview_update.set_model(model)
            del model
            self.wTree.get_widget("window1").set_sensitive(True)
            wTree.get_widget("vpaned1").set_position(vpaned_position)
            gtk.gdk.threads_leave()

        except Exception, detail:
            print "-- Exception occured in the refresh thread: " + str(detail)
            log.writelines("-- Exception occured in the refresh thread: " + str(detail) + "\n")
            log.flush()
            gtk.gdk.threads_enter()
            self.statusIcon.set_from_file(icon_error)
            self.statusIcon.set_tooltip(_("Could not refresh the list of packages"))
            #self.statusIcon.set_blinking(False)
            self.wTree.get_widget("window1").window.set_cursor(None)
            self.wTree.get_widget("window1").set_sensitive(True)
            statusbar.push(context_id, _("Could not refresh the list of packages"))
            wTree.get_widget("vpaned1").set_position(vpaned_position)
            gtk.gdk.threads_leave()

    def checkDependencies(self, changes, cache):
        foundSomething = False
        for pkg in changes:
            for dep in pkg.candidateDependencies:
                for o in dep.or_dependencies:
                    try:
                        if cache[o.name].isUpgradable:
                            pkgFound = False
                            for pkg2 in changes:
                                if o.name == pkg2.name:
                                    pkgFound = True
                            if pkgFound == False:
                                newPkg = cache[o.name]
                                changes.append(newPkg)
                                foundSomething = True
                    except Exception, detail:
                        pass # don't know why we get these..
        if (foundSomething):
            changes = self.checkDependencies(changes, cache)
        return changes

def force_refresh(widget, treeview, statusIcon, wTree):
    refresh = RefreshThread(treeview, statusIcon, wTree)
    refresh.start()

def clear(widget, treeView, statusbar, context_id):
    model = treeView.get_model()
    iter = model.get_iter_first()
    while (iter != None):
        model.set_value(iter, 0, "false")
        iter = model.iter_next(iter)
    statusbar.push(context_id, _("No updates selected"))

def select_all(widget, treeView, statusbar, context_id):
    model = treeView.get_model()
    iter = model.get_iter_first()
    while (iter != None):
        model.set_value(iter, 0, "true")
        iter = model.iter_next(iter)
    iter = model.get_iter_first()
    download_size = 0
    num_selected = 0
    while (iter != None):
        checked = model.get_value(iter, 0)
        if (checked == "true"):            
            size = model.get_value(iter, 9)
            download_size = download_size + size
            num_selected = num_selected + 1                                
        iter = model.iter_next(iter)
    if num_selected == 0:
        statusbar.push(context_id, _("No updates selected"))
    elif num_selected == 1:
        statusbar.push(context_id, _("%(selected)d update selected (%(size)s)") % {'selected':num_selected, 'size':size_to_string(download_size)})
    else:
        statusbar.push(context_id, _("%(selected)d updates selected (%(size)s)") % {'selected':num_selected, 'size':size_to_string(download_size)})

def install(widget, treeView, statusIcon, wTree):
    install = InstallThread(treeView, statusIcon, wTree)
    install.start()

def change_icon(widget, button, prefs_tree, treeview, statusIcon, wTree):
    global icon_busy
    global icon_up2date
    global icon_updates
    global icon_error
    global icon_unknown
    global icon_apply
    dialog = gtk.FileChooserDialog(_("Update Manager"), None, gtk.FILE_CHOOSER_ACTION_OPEN, (gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL, gtk.STOCK_OPEN, gtk.RESPONSE_OK))
    filter1 = gtk.FileFilter()
    filter1.set_name("*.*")
    filter1.add_pattern("*")
    filter2 = gtk.FileFilter()
    filter2.set_name("*.png")
    filter2.add_pattern("*.png")
    dialog.add_filter(filter2)
    dialog.add_filter(filter1)

    if dialog.run() == gtk.RESPONSE_OK:
        filename = dialog.get_filename()
        if (button == "busy"):
            prefs_tree.get_widget("image_busy").set_from_pixbuf(gtk.gdk.pixbuf_new_from_file_at_size(filename, 24, 24))
            icon_busy = filename
        if (button == "up2date"):
            prefs_tree.get_widget("image_up2date").set_from_pixbuf(gtk.gdk.pixbuf_new_from_file_at_size(filename, 24, 24))
            icon_up2date = filename
        if (button == "updates"):
            prefs_tree.get_widget("image_updates").set_from_pixbuf(gtk.gdk.pixbuf_new_from_file_at_size(filename, 24, 24))
            icon_updates = filename
        if (button == "error"):
            prefs_tree.get_widget("image_error").set_from_pixbuf(gtk.gdk.pixbuf_new_from_file_at_size(filename, 24, 24))
            icon_error = filename
        if (button == "unknown"):
            prefs_tree.get_widget("image_unknown").set_from_pixbuf(gtk.gdk.pixbuf_new_from_file_at_size(filename, 24, 24))
            icon_unknown = filename
        if (button == "apply"):
            prefs_tree.get_widget("image_apply").set_from_pixbuf(gtk.gdk.pixbuf_new_from_file_at_size(filename, 24, 24))
            icon_apply = filename
    dialog.destroy()

def pref_apply(widget, prefs_tree, treeview, statusIcon, wTree):
    global icon_busy
    global icon_up2date
    global icon_updates
    global icon_error
    global icon_unknown
    global icon_apply

    if (not os.path.exists("/etc/linuxmint")):
        os.system("mkdir -p /etc/linuxmint")
        global log
        log.writelines("++ Creating /etc/linuxmint directory\n")
        log.flush()

    config = ConfigObj("/etc/linuxmint/mintUpdate.conf")

    #Write level config
    config['levels'] = {}
    config['levels']['level1_visible'] = prefs_tree.get_widget("visible1").get_active()
    config['levels']['level2_visible'] = prefs_tree.get_widget("visible2").get_active()
    config['levels']['level3_visible'] = prefs_tree.get_widget("visible3").get_active()
    config['levels']['level4_visible'] = prefs_tree.get_widget("visible4").get_active()
    config['levels']['level5_visible'] = prefs_tree.get_widget("visible5").get_active()
    config['levels']['level1_safe'] = prefs_tree.get_widget("safe1").get_active()
    config['levels']['level2_safe'] = prefs_tree.get_widget("safe2").get_active()
    config['levels']['level3_safe'] = prefs_tree.get_widget("safe3").get_active()
    config['levels']['level4_safe'] = prefs_tree.get_widget("safe4").get_active()
    config['levels']['level5_safe'] = prefs_tree.get_widget("safe5").get_active()

    #Write refresh config
    config['refresh'] = {}
    config['refresh']['timer_minutes'] = int(prefs_tree.get_widget("timer_minutes").get_value())
    config['refresh']['timer_hours'] = int(prefs_tree.get_widget("timer_hours").get_value())
    config['refresh']['timer_days'] = int(prefs_tree.get_widget("timer_days").get_value())

    #Write update config
    config['update'] = {}
    config['update']['delay'] = str(int(prefs_tree.get_widget("spin_delay").get_value()))
    config['update']['ping_domain'] = prefs_tree.get_widget("text_ping").get_text()
    config['update']['dist_upgrade'] = prefs_tree.get_widget("checkbutton_dist_upgrade").get_active()

    #Write icons config
    config['icons'] = {}
    config['icons']['busy'] = icon_busy
    config['icons']['up2date'] = icon_up2date
    config['icons']['updates'] = icon_updates
    config['icons']['error'] = icon_error
    config['icons']['unknown'] = icon_unknown
    config['icons']['apply'] = icon_apply
    
    #Write blacklisted packages
    ignored_list = open("/etc/linuxmint/mintupdate.ignored", "w")
    treeview_blacklist = prefs_tree.get_widget("treeview_blacklist")
    model = treeview_blacklist.get_model()
    iter = model.get_iter_first()
    while iter is not None:
        pkg = model.get_value(iter, 0)
        iter = model.iter_next(iter)
        ignored_list.writelines(pkg + "\n")
    ignored_list.close()

    config.write()

    prefs_tree.get_widget("window2").hide()
    refresh = RefreshThread(treeview, statusIcon, wTree)
    refresh.start()

def info_cancel(widget, prefs_tree):
    prefs_tree.get_widget("window3").hide()

def history_cancel(widget, tree):
    tree.get_widget("window4").hide()

def history_clear(widget, tree):
    os.system("rm -rf /var/log/mintUpdate.history")
    model = gtk.TreeStore(str, str, str, gtk.gdk.Pixbuf, str, str)
    tree.set_model(model)
    del model

def pref_cancel(widget, prefs_tree):
    prefs_tree.get_widget("window2").hide()

def read_configuration():
    global icon_busy
    global icon_up2date
    global icon_updates
    global icon_error
    global icon_unknown
    global icon_apply

    config = ConfigObj("/etc/linuxmint/mintUpdate.conf")
    prefs = {}

    #Read refresh config
    try:
        prefs["timer_minutes"] = int(config['refresh']['timer_minutes'])
        prefs["timer_hours"] = int(config['refresh']['timer_hours'])
        prefs["timer_days"] = int(config['refresh']['timer_days'])
    except:
        prefs["timer_minutes"] = 15
        prefs["timer_hours"] = 0
        prefs["timer_days"] = 0

    #Read update config
    try:
        prefs["delay"] = int(config['update']['delay'])
        prefs["ping_domain"] = config['update']['ping_domain']
        prefs["dist_upgrade"] = (config['update']['dist_upgrade'] == "True")
    except:
        prefs["delay"] = 30
        prefs["ping_domain"] = "google.com"
        prefs["dist_upgrade"] = True

    #Read icons config
    try:
        icon_busy = config['icons']['busy']
        icon_up2date = config['icons']['up2date']
        icon_updates = config['icons']['updates']
        icon_error = config['icons']['error']
        icon_unknown = config['icons']['unknown']
        icon_apply = config['icons']['apply']
    except:
        icon_busy = "/usr/lib/linuxmint/mintUpdate/icons/base.svg"
        icon_up2date = "/usr/lib/linuxmint/mintUpdate/icons/base-apply.svg"
        icon_updates = "/usr/lib/linuxmint/mintUpdate/icons/base-info.svg"
        icon_error = "/usr/lib/linuxmint/mintUpdate/icons/base-error2.svg"
        icon_unknown = "/usr/lib/linuxmint/mintUpdate/icons/base.svg"
        icon_apply = "/usr/lib/linuxmint/mintUpdate/icons/base-exec.svg"

    #Read levels config
    try:
        prefs["level1_visible"] = (config['levels']['level1_visible'] == "True")
        prefs["level2_visible"] = (config['levels']['level2_visible'] == "True")
        prefs["level3_visible"] = (config['levels']['level3_visible'] == "True")
        prefs["level4_visible"] = (config['levels']['level4_visible'] == "True")
        prefs["level5_visible"] = (config['levels']['level5_visible'] == "True")
        prefs["level1_safe"] = (config['levels']['level1_safe'] == "True")
        prefs["level2_safe"] = (config['levels']['level2_safe'] == "True")
        prefs["level3_safe"] = (config['levels']['level3_safe'] == "True")
        prefs["level4_safe"] = (config['levels']['level4_safe'] == "True")
        prefs["level5_safe"] = (config['levels']['level5_safe'] == "True")
    except:
        prefs["level1_visible"] = True
        prefs["level2_visible"] = True
        prefs["level3_visible"] = True
        prefs["level4_visible"] = False
        prefs["level5_visible"] = False
        prefs["level1_safe"] = True
        prefs["level2_safe"] = True
        prefs["level3_safe"] = True
        prefs["level4_safe"] = False
        prefs["level5_safe"] = False    

    #Read columns config
    try:
        prefs["level_column_visible"] = (config['visible_columns']['level'] == "True")
    except:
        prefs["level_column_visible"] = True
    try:
        prefs["package_column_visible"] = (config['visible_columns']['package'] == "True")
    except:
        prefs["package_column_visible"] = True
    try:
        prefs["old_version_column_visible"] = (config['visible_columns']['old_version'] == "True")
    except:
        prefs["old_version_column_visible"] = True
    try:
        prefs["new_version_column_visible"] = (config['visible_columns']['new_version'] == "True")
    except:
        prefs["new_version_column_visible"] = True
    try:
        prefs["size_column_visible"] = (config['visible_columns']['size'] == "True")
    except:
        prefs["size_column_visible"] = True

    #Read window dimensions
    try:
        prefs["dimensions_x"] = int(config['dimensions']['x'])
        prefs["dimensions_y"] = int(config['dimensions']['y'])
        prefs["dimensions_pane_position"] = int(config['dimensions']['pane_position'])
    except:
        prefs["dimensions_x"] = 790
        prefs["dimensions_y"] = 540
        prefs["dimensions_pane_position"] = 230

    #Read package blacklist
    try:
        prefs["blacklisted_packages"] = config['blacklisted_packages']
    except:
        prefs["blacklisted_packages"] = []

    return prefs

def open_repositories(widget):
    if os.path.exists("/usr/bin/software-properties-gtk"):
        os.system("/usr/bin/software-properties-gtk &")
    elif os.path.exists("/usr/bin/software-properties-kde"):
        os.system("/usr/bin/software-properties-kde &")

def open_preferences(widget, treeview, statusIcon, wTree):
    global icon_busy
    global icon_up2date
    global icon_updates
    global icon_error
    global icon_unknown
    global icon_apply

    gladefile = "/usr/lib/linuxmint/mintUpdate/mintUpdate.glade"
    prefs_tree = gtk.glade.XML(gladefile, "window2")
    prefs_tree.get_widget("window2").set_title(_("Preferences") + " - " + _("Update Manager"))

    prefs_tree.get_widget("label37").set_text(_("Levels"))
    prefs_tree.get_widget("label36").set_text(_("Auto-Refresh"))
    prefs_tree.get_widget("label39").set_markup("<b>" + _("Level") + "</b>")
    prefs_tree.get_widget("label40").set_markup("<b>" + _("Description") + "</b>")
    prefs_tree.get_widget("label48").set_markup("<b>" + _("Tested?") + "</b>")
    prefs_tree.get_widget("label54").set_markup("<b>" + _("Origin") + "</b>")
    prefs_tree.get_widget("label41").set_markup("<b>" + _("Safe?") + "</b>")
    prefs_tree.get_widget("label42").set_markup("<b>" + _("Visible?") + "</b>")
    prefs_tree.get_widget("label43").set_text(_("Certified packages. Tested through Romeo or directly maintained by Linux Mint."))
    prefs_tree.get_widget("label44").set_text(_("Recommended packages. Tested and approved by Linux Mint."))
    prefs_tree.get_widget("label45").set_text(_("Safe packages. Not tested but believed to be safe."))
    prefs_tree.get_widget("label46").set_text(_("Unsafe packages. Could potentially affect the stability of the system."))
    prefs_tree.get_widget("label47").set_text(_("Dangerous packages. Known to affect the stability of the systems depending on certain specs or hardware."))
    prefs_tree.get_widget("label55").set_text(_("Linux Mint"))
    prefs_tree.get_widget("label56").set_text(_("Upstream"))
    prefs_tree.get_widget("label57").set_text(_("Upstream"))
    prefs_tree.get_widget("label58").set_text(_("Upstream"))
    prefs_tree.get_widget("label59").set_text(_("Upstream"))
    prefs_tree.get_widget("label81").set_text(_("Refresh the list of updates every:"))
    prefs_tree.get_widget("label82").set_text("<i>" + _("Note: The list only gets refreshed while the update manager window is closed (system tray mode).") + "</i>")
    prefs_tree.get_widget("label82").set_use_markup(True)
    prefs_tree.get_widget("label83").set_text(_("Update Method"))
    prefs_tree.get_widget("label84").set_text("<i>" + _("Note: The dist-upgrade option, in addition to performing the function of upgrade, also intelligently handles changing dependencies with new versions of packages. Without this option, only the latest versions of any out-of-date packages on your system are installed. Packages that are not yet installed don't get installed automatically and newer versions of packages which dependencies require such installations are simply ignored.") + "</i>")
    prefs_tree.get_widget("label84").set_use_markup(True)
    prefs_tree.get_widget("label85").set_text(_("Icons"))
    prefs_tree.get_widget("label86").set_markup("<b>" + _("Icon") + "</b>")
    prefs_tree.get_widget("label87").set_markup("<b>" + _("Status") + "</b>")
    prefs_tree.get_widget("label95").set_markup("<b>" + _("New Icon") + "</b>")
    prefs_tree.get_widget("label88").set_text(_("Busy"))
    prefs_tree.get_widget("label89").set_text(_("System up-to-date"))
    prefs_tree.get_widget("label90").set_text(_("Updates available"))
    prefs_tree.get_widget("label99").set_text(_("Error"))
    prefs_tree.get_widget("label2").set_text(_("Unknown state"))
    prefs_tree.get_widget("label3").set_text(_("Applying updates"))
    prefs_tree.get_widget("label6").set_text(_("Startup delay (in seconds):"))
    prefs_tree.get_widget("label7").set_text(_("Internet check (domain name or IP address):"))
    prefs_tree.get_widget("label1").set_text(_("Ignored packages"))

    prefs_tree.get_widget("checkbutton_dist_upgrade").set_label(_("Satisfy changing dependencies using dist-upgrade?"))

    prefs_tree.get_widget("window2").set_icon_from_file("/usr/lib/linuxmint/mintUpdate/icons/base.svg")
    prefs_tree.get_widget("window2").show()
    prefs_tree.get_widget("pref_button_cancel").connect("clicked", pref_cancel, prefs_tree)
    prefs_tree.get_widget("pref_button_apply").connect("clicked", pref_apply, prefs_tree, treeview, statusIcon, wTree)

    prefs_tree.get_widget("button_icon_busy").connect("clicked", change_icon, "busy", prefs_tree, treeview, statusIcon, wTree)
    prefs_tree.get_widget("button_icon_up2date").connect("clicked", change_icon, "up2date", prefs_tree, treeview, statusIcon, wTree)
    prefs_tree.get_widget("button_icon_updates").connect("clicked", change_icon, "updates", prefs_tree, treeview, statusIcon, wTree)
    prefs_tree.get_widget("button_icon_error").connect("clicked", change_icon, "error", prefs_tree, treeview, statusIcon, wTree)
    prefs_tree.get_widget("button_icon_unknown").connect("clicked", change_icon, "unknown", prefs_tree, treeview, statusIcon, wTree)
    prefs_tree.get_widget("button_icon_apply").connect("clicked", change_icon, "apply", prefs_tree, treeview, statusIcon, wTree)

    prefs = read_configuration()

    prefs_tree.get_widget("visible1").set_active(prefs["level1_visible"])
    prefs_tree.get_widget("visible2").set_active(prefs["level2_visible"])
    prefs_tree.get_widget("visible3").set_active(prefs["level3_visible"])
    prefs_tree.get_widget("visible4").set_active(prefs["level4_visible"])
    prefs_tree.get_widget("visible5").set_active(prefs["level5_visible"])
    prefs_tree.get_widget("safe1").set_active(prefs["level1_safe"])
    prefs_tree.get_widget("safe2").set_active(prefs["level2_safe"])
    prefs_tree.get_widget("safe3").set_active(prefs["level3_safe"])
    prefs_tree.get_widget("safe4").set_active(prefs["level4_safe"])
    prefs_tree.get_widget("safe5").set_active(prefs["level5_safe"])

    prefs_tree.get_widget("timer_minutes_label").set_text(_("minutes"))
    prefs_tree.get_widget("timer_hours_label").set_text(_("hours"))
    prefs_tree.get_widget("timer_days_label").set_text(_("days"))
    prefs_tree.get_widget("timer_minutes").set_value(prefs["timer_minutes"])
    prefs_tree.get_widget("timer_hours").set_value(prefs["timer_hours"])
    prefs_tree.get_widget("timer_days").set_value(prefs["timer_days"])

    prefs_tree.get_widget("text_ping").set_text(prefs["ping_domain"])

    prefs_tree.get_widget("spin_delay").set_value(prefs["delay"])

    prefs_tree.get_widget("checkbutton_dist_upgrade").set_active(prefs["dist_upgrade"])

    prefs_tree.get_widget("image_busy").set_from_pixbuf(gtk.gdk.pixbuf_new_from_file_at_size(icon_busy, 24, 24))
    prefs_tree.get_widget("image_up2date").set_from_pixbuf(gtk.gdk.pixbuf_new_from_file_at_size(icon_up2date, 24, 24))
    prefs_tree.get_widget("image_updates").set_from_pixbuf(gtk.gdk.pixbuf_new_from_file_at_size(icon_updates, 24, 24))
    prefs_tree.get_widget("image_error").set_from_pixbuf(gtk.gdk.pixbuf_new_from_file_at_size(icon_error, 24, 24))
    prefs_tree.get_widget("image_unknown").set_from_pixbuf(gtk.gdk.pixbuf_new_from_file_at_size(icon_unknown, 24, 24))
    prefs_tree.get_widget("image_apply").set_from_pixbuf(gtk.gdk.pixbuf_new_from_file_at_size(icon_apply, 24, 24))

    # Blacklisted packages
    treeview_blacklist = prefs_tree.get_widget("treeview_blacklist")
    column1 = gtk.TreeViewColumn(_("Ignored packages"), gtk.CellRendererText(), text=0)
    column1.set_sort_column_id(0)
    column1.set_resizable(True)
    treeview_blacklist.append_column(column1)
    treeview_blacklist.set_headers_clickable(True)
    treeview_blacklist.set_reorderable(False)
    treeview_blacklist.show()

    model = gtk.TreeStore(str)
    model.set_sort_column_id( 0, gtk.SORT_ASCENDING )
    treeview_blacklist.set_model(model)

    if os.path.exists("/etc/linuxmint/mintupdate.ignored"):
        ignored_list = open("/etc/linuxmint/mintupdate.ignored", "r")
        for ignored_pkg in ignored_list:            
            iter = model.insert_before(None, None)
            model.set_value(iter, 0, ignored_pkg.strip())
        del model
        ignored_list.close()
    
    prefs_tree.get_widget("toolbutton_add").connect("clicked", add_blacklisted_package, treeview_blacklist)
    prefs_tree.get_widget("toolbutton_remove").connect("clicked", remove_blacklisted_package, treeview_blacklist)

def add_blacklisted_package(widget, treeview_blacklist):

    dialog = gtk.MessageDialog(None, gtk.DIALOG_MODAL | gtk.DIALOG_DESTROY_WITH_PARENT, gtk.MESSAGE_QUESTION, gtk.BUTTONS_OK, None)
    dialog.set_markup("<b>" + _("Please enter a package name:") + "</b>")
    dialog.set_title(_("Ignore a package"))
    dialog.set_icon_from_file("/usr/lib/linuxmint/mintUpdate/icons/base.svg")
    entry = gtk.Entry()
    hbox = gtk.HBox()
    hbox.pack_start(gtk.Label(_("Package name:")), False, 5, 5)
    hbox.pack_end(entry)
    dialog.format_secondary_markup("<i>" + _("All available upgrades for this package will be ignored.") + "</i>")
    dialog.vbox.pack_end(hbox, True, True, 0)
    dialog.show_all()
    dialog.run()
    name = entry.get_text()
    dialog.destroy()
    pkg = name.strip()
    if pkg != '':
        model = treeview_blacklist.get_model()
        iter = model.insert_before(None, None)
        model.set_value(iter, 0, pkg)

def remove_blacklisted_package(widget, treeview_blacklist):
    selection = treeview_blacklist.get_selection()
    (model, iter) = selection.get_selected()
    if (iter != None):
        pkg = model.get_value(iter, 0)
        model.remove(iter)

def open_history(widget):
    #Set the Glade file
    gladefile = "/usr/lib/linuxmint/mintUpdate/mintUpdate.glade"
    wTree = gtk.glade.XML(gladefile, "window4")
    treeview_update = wTree.get_widget("treeview_history")
    wTree.get_widget("window4").set_icon_from_file("/usr/lib/linuxmint/mintUpdate/icons/base.svg")

    wTree.get_widget("window4").set_title(_("History of updates") + " - " + _("Update Manager"))

    # the treeview
    column1 = gtk.TreeViewColumn(_("Date"), gtk.CellRendererText(), text=1)
    column1.set_sort_column_id(1)
    column1.set_resizable(True)
    column2 = gtk.TreeViewColumn(_("Package"), gtk.CellRendererText(), text=2)
    column2.set_sort_column_id(2)
    column2.set_resizable(True)
    column3 = gtk.TreeViewColumn(_("Level"), gtk.CellRendererPixbuf(), pixbuf=3)
    column3.set_sort_column_id(6)
    column3.set_resizable(True)
    column4 = gtk.TreeViewColumn(_("Old version"), gtk.CellRendererText(), text=4)
    column4.set_sort_column_id(4)
    column4.set_resizable(True)
    column5 = gtk.TreeViewColumn(_("New version"), gtk.CellRendererText(), text=5)
    column5.set_sort_column_id(5)
    column5.set_resizable(True)

    treeview_update.append_column(column1)
    treeview_update.append_column(column3)
    treeview_update.append_column(column2)
    treeview_update.append_column(column5)
    treeview_update.append_column(column4)

    treeview_update.set_headers_clickable(True)
    treeview_update.set_reorderable(False)
    treeview_update.set_search_column(2)
    treeview_update.set_enable_search(True)
    treeview_update.show()

    model = gtk.TreeStore(str, str, str, gtk.gdk.Pixbuf, str, str, str) # (date, packageName, level, oldVersion, newVersion, stringLevel)
    if (os.path.exists("/var/log/mintUpdate.history")):
        updates = commands.getoutput("cat /var/log/mintUpdate.history")
        updates = string.split(updates, "\n")
        for pkg in updates:
            values = string.split(pkg, "\t")
            if len(values) == 5:
                date = values[0]
                package = values[1]
                level = values[2]
                oldVersion = values[3]
                newVersion = values[4]

                iter = model.insert_before(None, None)
                model.set_value(iter, 0, package)
                model.row_changed(model.get_path(iter), iter)
                model.set_value(iter, 1, date)
                model.set_value(iter, 2, package)
                model.set_value(iter, 3, gtk.gdk.pixbuf_new_from_file("/usr/lib/linuxmint/mintUpdate/icons/level" + str(level) + ".png"))
                model.set_value(iter, 4, oldVersion)
                model.set_value(iter, 5, newVersion)
                model.set_value(iter, 6, level)

    treeview_update.set_model(model)
    del model
    wTree.get_widget("button_close").connect("clicked", history_cancel, wTree)
    wTree.get_widget("button_clear").connect("clicked", history_clear, treeview_update)

def open_information(widget):
    global logFile
    global mode
    global pid

    gladefile = "/usr/lib/linuxmint/mintUpdate/mintUpdate.glade"
    prefs_tree = gtk.glade.XML(gladefile, "window3")
    prefs_tree.get_widget("window3").set_title(_("Information") + " - " + _("Update Manager"))
    prefs_tree.get_widget("window3").set_icon_from_file("/usr/lib/linuxmint/mintUpdate/icons/base.svg")
    prefs_tree.get_widget("close_button").connect("clicked", info_cancel, prefs_tree)
    prefs_tree.get_widget("label1").set_text(_("Information"))
    prefs_tree.get_widget("label2").set_text(_("Log file"))
    prefs_tree.get_widget("label3").set_text(_("Permissions:"))
    prefs_tree.get_widget("label4").set_text(_("Process ID:"))
    prefs_tree.get_widget("label5").set_text(_("Log file:"))

    prefs_tree.get_widget("mode_label").set_text(str(mode))
    prefs_tree.get_widget("processid_label").set_text(str(pid))
    prefs_tree.get_widget("log_filename").set_text(str(logFile))
    txtbuffer = gtk.TextBuffer()
    txtbuffer.set_text(commands.getoutput("cat " + logFile))
    prefs_tree.get_widget("log_textview").set_buffer(txtbuffer)

def open_about(widget):
    dlg = gtk.AboutDialog()
    dlg.set_title(_("About") + " - " + _("Update Manager"))
    dlg.set_program_name("mintUpdate")
    dlg.set_comments(_("Update Manager"))
    try:
        h = open('/usr/share/common-licenses/GPL','r')
        s = h.readlines()
        gpl = ""
        for line in s:
            gpl += line
        h.close()
        dlg.set_license(gpl)
    except Exception, detail:
        print detail
    try:
        version = commands.getoutput("/usr/lib/linuxmint/common/version.py mintupdate")
        dlg.set_version(version)
    except Exception, detail:
        print detail

    dlg.set_authors(["Clement Lefebvre <root@linuxmint.com>", "Chris Hodapp <clhodapp@live.com>"])
    dlg.set_icon_from_file("/usr/lib/linuxmint/mintUpdate/icons/base.svg")
    dlg.set_logo(gtk.gdk.pixbuf_new_from_file("/usr/lib/linuxmint/mintUpdate/icons/base.svg"))
    def close(w, res):
        if res == gtk.RESPONSE_CANCEL:
            w.hide()
    dlg.connect("response", close)
    dlg.show()

def quit_cb(widget, window, vpaned, data = None):
    global log
    if data:
        data.set_visible(False)
    try:
        log.writelines("++ Exiting - requested by user\n")
        log.flush()
        log.close()
        save_window_size(window, vpaned)
    except:
        pass # cause log might already been closed
    # Whatever works best heh :) 
    pid = os.getpid()    
    os.system("kill -9 %s &" % pid)
    #gtk.main_quit()
    #sys.exit(0)

def info_cb(widget, data = None):
    global log
    global logFile
    if data:
        data.set_visible(False)
    try:
        log.flush()
        os.system("gedit " + logFile)
    except:
        pass

def popup_menu_cb(widget, button, time, data = None):
    if button == 3:
        if data:
            data.show_all()
            data.popup(None, None, None, 3, time)
    pass

def close_window(window, event, vpaned):
    global app_hidden
    window.hide()
    save_window_size(window, vpaned)
    app_hidden = True
    return True

def hide_window(widget, window):
    global app_hidden
    window.hide()
    app_hidden = True

def activate_icon_cb(widget, data, wTree, pid):
    global app_hidden
    if (app_hidden == True):
            # check credentials
        if os.getuid() != 0 :
            try:
                log.writelines("++ Launching mintUpdate in root mode, waiting for it to kill us...\n")
                log.flush()
                log.close()
            except:
                pass #cause we might have closed it already
            os.system("gksudo --message \"" + _("Please enter your password to start the update manager") + "\" /usr/lib/linuxmint/mintUpdate/mintUpdate.py show " + str(pid) + " &")
        else:
            wTree.get_widget("window1").show()
            app_hidden = False
    else:
        wTree.get_widget("window1").hide()
        app_hidden = True
        save_window_size(wTree.get_widget("window1"), wTree.get_widget("vpaned1"))

def save_window_size(window, vpaned):

    config = ConfigObj("/etc/linuxmint/mintUpdate.conf")
    config['dimensions'] = {}
    config['dimensions']['x'] = window.get_size()[0]
    config['dimensions']['y'] = window.get_size()[1]
    config['dimensions']['pane_position'] = vpaned.get_position()
    config.write()

def display_selected_package(selection, wTree):    
    wTree.get_widget("textview_description").get_buffer().set_text("")
    wTree.get_widget("textview_changes").get_buffer().set_text("")            
    (model, iter) = selection.get_selected()
    if (iter != None):
        selected_package = model.get_value(iter, 1)
        description_txt = model.get_value(iter, 8)                
        wTree.get_widget("notebook_details").set_current_page(0)
        wTree.get_widget("textview_description").get_buffer().set_text(description_txt)

def switch_page(notebook, page, page_num, Wtree, treeView):
    selection = treeView.get_selection()
    (model, iter) = selection.get_selected()
    if (iter != None):
        selected_package = model.get_value(iter, 1)
        description_txt = model.get_value(iter, 8)   
        if (page_num == 0):
            # Description tab
            wTree.get_widget("textview_description").get_buffer().set_text(description_txt)
        if (page_num == 1):
            # Changelog tab            
            level = model.get_value(iter, 7)
            version = model.get_value(iter, 4)
            retriever = ChangelogRetriever(selected_package, level, version, wTree)
            retriever.start()

def celldatafunction_checkbox(column, cell, model, iter):
    cell.set_property("activatable", True)
    checked = model.get_value(iter, 0)
    if (checked == "true"):
        cell.set_property("active", True)
    else:
        cell.set_property("active", False)

def toggled(renderer, path, treeview, statusbar, context_id):
    model = treeview.get_model()
    iter = model.get_iter(path)
    if (iter != None):
        checked = model.get_value(iter, 0)
        if (checked == "true"):
            model.set_value(iter, 0, "false")
        else:
            model.set_value(iter, 0, "true")
    
    iter = model.get_iter_first()
    download_size = 0
    num_selected = 0
    while (iter != None):
        checked = model.get_value(iter, 0)
        if (checked == "true"):            
            size = model.get_value(iter, 9)
            download_size = download_size + size
            num_selected = num_selected + 1                                
        iter = model.iter_next(iter)
    if num_selected == 0:
        statusbar.push(context_id, _("No updates selected"))
    elif num_selected == 1:
        statusbar.push(context_id, _("%(selected)d update selected (%(size)s)") % {'selected':num_selected, 'size':size_to_string(download_size)})
    else:
        statusbar.push(context_id, _("%(selected)d updates selected (%(size)s)") % {'selected':num_selected, 'size':size_to_string(download_size)})
    
def size_to_string(size):
    strSize = str(size) + _("B")
    if (size >= 1024):
        strSize = str(size / 1024) + _("KB")
    if (size >= (1024 * 1024)):
        strSize = str(size / (1024 * 1024)) + _("MB")
    if (size >= (1024 * 1024 * 1024)):
        strSize = str(size / (1024 * 1024 * 1024)) + _("GB")
    return strSize

def setVisibleColumn(checkmenuitem, column, configName):
    config = ConfigObj("/etc/linuxmint/mintUpdate.conf")
    if (config.has_key('visible_columns')):
        config['visible_columns'][configName] = checkmenuitem.get_active()
    else:
        config['visible_columns'] = {}
        config['visible_columns'][configName] = checkmenuitem.get_active()
    config.write()
    column.set_visible(checkmenuitem.get_active())
    
def menuPopup(widget, event, treeview_update, statusIcon, wTree):
    if event.button == 3:
        (model, iter) = widget.get_selection().get_selected()
        if (iter != None):
            selected_package = model.get_value(iter, 1)
            menu = gtk.Menu()                
            menuItem = gtk.MenuItem(_("Ignore updates for this package"))
            menuItem.connect("activate", add_to_ignore_list, treeview_update, selected_package, statusIcon, wTree)
            menu.append(menuItem)        
            menu.show_all()        
            menu.popup( None, None, None, 3, 0)
        
def add_to_ignore_list(widget, treeview_update, pkg, statusIcon, wTree):
    os.system("echo \"%s\" >> /etc/linuxmint/mintupdate.ignored" % pkg)
    force_refresh(widget, treeview_update, statusIcon, wTree)

global app_hiden
global log
global logFile
global mode
global pid
global statusbar
global context_id

app_hidden = True

gtk.gdk.threads_init()

parentPid = "0"
if len(sys.argv) > 2:
    parentPid = sys.argv[2]
    if (parentPid != "0"):
        os.system("kill -9 " + parentPid)

#

# prepare the log
pid = os.getpid()
logdir = "/tmp/mintUpdate/"
if os.getuid() == 0 :

    mode = "root"
else:
    mode = "user"
os.system("mkdir -p " + logdir)
#logFile = logdir + "/" + parentPid + "_" + str(pid) + ".log"
#log = open(logFile, "w")
log = tempfile.NamedTemporaryFile(prefix = logdir, delete=False)
logFile = log.name

log.writelines("++ Launching mintUpdate in " + mode + " mode\n")
log.flush()

try:
    global icon_busy
    global icon_up2date
    global icon_updates
    global icon_error
    global icon_unknown
    global icon_apply

    prefs = read_configuration()

    statusIcon = gtk.StatusIcon()
    statusIcon.set_from_file(icon_busy)
    statusIcon.set_tooltip(_("Checking for updates"))
    statusIcon.set_visible(True)    

    #Set the Glade file
    gladefile = "/usr/lib/linuxmint/mintUpdate/mintUpdate.glade"
    wTree = gtk.glade.XML(gladefile, "window1")
    wTree.get_widget("window1").set_title(_("Update Manager"))
    wTree.get_widget("window1").set_default_size(prefs['dimensions_x'], prefs['dimensions_y'])
    wTree.get_widget("vpaned1").set_position(prefs['dimensions_pane_position'])
    
    statusbar = wTree.get_widget("statusbar")
    context_id = statusbar.get_context_id("mintUpdate")

    vbox = wTree.get_widget("vbox_main")
    treeview_update = wTree.get_widget("treeview_update")
    wTree.get_widget("window1").set_icon_from_file("/usr/lib/linuxmint/mintUpdate/icons/base.svg")

    # Get the window socket (needed for synaptic later on)
    socket = gtk.Socket()
    if os.getuid() != 0 :
        # If we're not in root mode do that (don't know why it's needed.. very weird)
        vbox.pack_start(socket, True, True, 0)
    socket.show()
    window_id = repr(socket.get_id())

    # the treeview
    cr = gtk.CellRendererToggle()
    cr.connect("toggled", toggled, treeview_update, statusbar, context_id)
    column1 = gtk.TreeViewColumn(_("Upgrade"), cr)
    column1.set_cell_data_func(cr, celldatafunction_checkbox)
    column1.set_sort_column_id(5)
    column1.set_resizable(True)

    column2 = gtk.TreeViewColumn(_("Package"), gtk.CellRendererText(), text=1)
    column2.set_sort_column_id(1)
    column2.set_resizable(True)

    column3 = gtk.TreeViewColumn(_("Level"), gtk.CellRendererPixbuf(), pixbuf=2)
    column3.set_sort_column_id(7)
    column3.set_resizable(True)

    column4 = gtk.TreeViewColumn(_("Old version"), gtk.CellRendererText(), text=3)
    column4.set_sort_column_id(3)
    column4.set_resizable(True)

    column5 = gtk.TreeViewColumn(_("New version"), gtk.CellRendererText(), text=4)
    column5.set_sort_column_id(4)
    column5.set_resizable(True)

    column6 = gtk.TreeViewColumn(_("Size"), gtk.CellRendererText(), text=10)
    column6.set_sort_column_id(9)
    column6.set_resizable(True)

    treeview_update.append_column(column3)
    treeview_update.append_column(column1)
    treeview_update.append_column(column2)
    treeview_update.append_column(column5)
    treeview_update.append_column(column4)
    treeview_update.append_column(column6)
    treeview_update.set_headers_clickable(True)
    treeview_update.set_reorderable(False)
    treeview_update.show()
    
    treeview_update.connect( "button-release-event", menuPopup, treeview_update, statusIcon, wTree )

    model = gtk.TreeStore(str, str, gtk.gdk.Pixbuf, str, str, str, str, str, int, str)
    model.set_sort_column_id( 7, gtk.SORT_ASCENDING )
    treeview_update.set_model(model)
    del model

    selection = treeview_update.get_selection()
    selection.connect("changed", display_selected_package, wTree)
    wTree.get_widget("notebook_details").connect("switch-page", switch_page, wTree, treeview_update)
    wTree.get_widget("window1").connect("delete_event", close_window, wTree.get_widget("vpaned1"))
    wTree.get_widget("tool_apply").connect("clicked", install, treeview_update, statusIcon, wTree)
    wTree.get_widget("tool_clear").connect("clicked", clear, treeview_update, statusbar, context_id)
    wTree.get_widget("tool_select_all").connect("clicked", select_all, treeview_update, statusbar, context_id)
    wTree.get_widget("tool_refresh").connect("clicked", force_refresh, treeview_update, statusIcon, wTree)

    menu = gtk.Menu()
    menuItem3 = gtk.ImageMenuItem(gtk.STOCK_REFRESH)
    menuItem3.connect('activate', force_refresh, treeview_update, statusIcon, wTree)
    menu.append(menuItem3)
    menuItem2 = gtk.ImageMenuItem(gtk.STOCK_DIALOG_INFO)
    menuItem2.connect('activate', open_information)
    menu.append(menuItem2)
    if os.getuid() == 0 :
        menuItem4 = gtk.ImageMenuItem(gtk.STOCK_PREFERENCES)
        menuItem4.connect('activate', open_preferences, treeview_update, statusIcon, wTree)
        menu.append(menuItem4)
    menuItem = gtk.ImageMenuItem(gtk.STOCK_QUIT)
    menuItem.connect('activate', quit_cb, wTree.get_widget("window1"), wTree.get_widget("vpaned1"), statusIcon)
    menu.append(menuItem)

    statusIcon.connect('activate', activate_icon_cb, None, wTree, pid)
    statusIcon.connect('popup-menu', popup_menu_cb, menu)

    # Set text for all visible widgets (because of i18n)
    wTree.get_widget("tool_apply").set_label(_("Install Updates"))
    wTree.get_widget("tool_refresh").set_label(_("Refresh"))
    wTree.get_widget("tool_select_all").set_label(_("Select All"))
    wTree.get_widget("tool_clear").set_label(_("Clear"))
    wTree.get_widget("label9").set_text(_("Description"))
    wTree.get_widget("label8").set_text(_("Changelog"))    

    wTree.get_widget("label_error_detail").set_text("")
    wTree.get_widget("hbox_error").hide()
    wTree.get_widget("scrolledwindow1").hide()
    wTree.get_widget("viewport1").hide()
    wTree.get_widget("label_error_detail").hide()
    wTree.get_widget("image_error").hide()
    wTree.get_widget("vpaned1").set_position(prefs['dimensions_pane_position'])

    fileMenu = gtk.MenuItem(_("_File"))
    fileSubmenu = gtk.Menu()
    fileMenu.set_submenu(fileSubmenu)
    closeMenuItem = gtk.ImageMenuItem(gtk.STOCK_CLOSE)
    closeMenuItem.get_child().set_text(_("Close"))
    closeMenuItem.connect("activate", hide_window, wTree.get_widget("window1"))
    fileSubmenu.append(closeMenuItem)

    editMenu = gtk.MenuItem(_("_Edit"))
    editSubmenu = gtk.Menu()
    editMenu.set_submenu(editSubmenu)
    prefsMenuItem = gtk.ImageMenuItem(gtk.STOCK_PREFERENCES)
    prefsMenuItem.get_child().set_text(_("Preferences"))
    prefsMenuItem.connect("activate", open_preferences, treeview_update, statusIcon, wTree)
    editSubmenu.append(prefsMenuItem)
    if os.path.exists("/usr/bin/software-properties-gtk") or os.path.exists("/usr/bin/software-properties-kde"):
        sourcesMenuItem = gtk.ImageMenuItem(gtk.STOCK_PREFERENCES)
        sourcesMenuItem.set_image(gtk.image_new_from_file("/usr/lib/linuxmint/mintUpdate/icons/software-properties.png"))
        sourcesMenuItem.get_child().set_text(_("Software sources"))
        sourcesMenuItem.connect("activate", open_repositories)
        editSubmenu.append(sourcesMenuItem)

    viewMenu = gtk.MenuItem(_("_View"))
    viewSubmenu = gtk.Menu()
    viewMenu.set_submenu(viewSubmenu)
    historyMenuItem = gtk.ImageMenuItem(gtk.STOCK_INDEX)
    historyMenuItem.get_child().set_text(_("History of updates"))
    historyMenuItem.connect("activate", open_history)
    infoMenuItem = gtk.ImageMenuItem(gtk.STOCK_DIALOG_INFO)
    infoMenuItem.get_child().set_text(_("Information"))
    infoMenuItem.connect("activate", open_information)
    visibleColumnsMenuItem = gtk.MenuItem(gtk.STOCK_DIALOG_INFO)
    visibleColumnsMenuItem.get_child().set_text(_("Visible columns"))
    visibleColumnsMenu = gtk.Menu()
    visibleColumnsMenuItem.set_submenu(visibleColumnsMenu)

    levelColumnMenuItem = gtk.CheckMenuItem(_("Level"))
    levelColumnMenuItem.set_active(prefs["level_column_visible"])
    column3.set_visible(prefs["level_column_visible"])
    levelColumnMenuItem.connect("toggled", setVisibleColumn, column3, "level")
    visibleColumnsMenu.append(levelColumnMenuItem)

    packageColumnMenuItem = gtk.CheckMenuItem(_("Package"))
    packageColumnMenuItem.set_active(prefs["package_column_visible"])
    column2.set_visible(prefs["package_column_visible"])
    packageColumnMenuItem.connect("toggled", setVisibleColumn, column2, "package")
    visibleColumnsMenu.append(packageColumnMenuItem)

    oldVersionColumnMenuItem = gtk.CheckMenuItem(_("Old version"))
    oldVersionColumnMenuItem.set_active(prefs["old_version_column_visible"])
    column4.set_visible(prefs["old_version_column_visible"])
    oldVersionColumnMenuItem.connect("toggled", setVisibleColumn, column4, "old_version")
    visibleColumnsMenu.append(oldVersionColumnMenuItem)

    newVersionColumnMenuItem = gtk.CheckMenuItem(_("New version"))
    newVersionColumnMenuItem.set_active(prefs["new_version_column_visible"])
    column5.set_visible(prefs["new_version_column_visible"])
    newVersionColumnMenuItem.connect("toggled", setVisibleColumn, column5, "new_version")
    visibleColumnsMenu.append(newVersionColumnMenuItem)

    sizeColumnMenuItem = gtk.CheckMenuItem(_("Size"))
    sizeColumnMenuItem.set_active(prefs["size_column_visible"])
    column6.set_visible(prefs["size_column_visible"])
    sizeColumnMenuItem.connect("toggled", setVisibleColumn, column6, "size")
    visibleColumnsMenu.append(sizeColumnMenuItem)

    viewSubmenu.append(visibleColumnsMenuItem)
    viewSubmenu.append(historyMenuItem)
    viewSubmenu.append(infoMenuItem)

    helpMenu = gtk.MenuItem(_("_Help"))
    helpSubmenu = gtk.Menu()
    helpMenu.set_submenu(helpSubmenu)
    aboutMenuItem = gtk.ImageMenuItem(gtk.STOCK_ABOUT)
    aboutMenuItem.get_child().set_text(_("About"))
    aboutMenuItem.connect("activate", open_about)
    helpSubmenu.append(aboutMenuItem)

    #browser.connect("activate", browser_callback)
    #browser.show()
    wTree.get_widget("menubar1").append(fileMenu)
    wTree.get_widget("menubar1").append(editMenu)
    wTree.get_widget("menubar1").append(viewMenu)
    wTree.get_widget("menubar1").append(helpMenu)

    if len(sys.argv) > 1:
        showWindow = sys.argv[1]
        if (showWindow == "show"):
            wTree.get_widget("window1").show_all()
            wTree.get_widget("label_error_detail").set_text("")
            wTree.get_widget("hbox_error").hide()
            wTree.get_widget("scrolledwindow1").hide()
            wTree.get_widget("viewport1").hide()
            wTree.get_widget("label_error_detail").hide()
            wTree.get_widget("image_error").hide()
            wTree.get_widget("vpaned1").set_position(prefs['dimensions_pane_position'])
            app_hidden = False

    if os.getuid() != 0 :        
        #test the network connection to delay mintUpdate in case we're not yet connected
        log.writelines("++ Testing initial connection\n")
        log.flush()
        try:
            from urllib import urlopen
            url=urlopen("http://google.com")
            url.read()
            url.close()
            log.writelines("++ Connection to the Internet successful (tried to read http://www.google.com)\n")
            log.flush()
        except Exception, detail:
            print detail
            if os.system("ping " + prefs["ping_domain"] + " -c1 -q"):
                log.writelines("-- No connection found (tried to read http://www.google.com and to ping " + prefs["ping_domain"] + ") - sleeping for " + str(prefs["delay"]) + " seconds\n")
                log.flush()
                time.sleep(prefs["delay"])
            else:
                log.writelines("++ Connection found - checking for updates\n")
                log.flush()


    wTree.get_widget("notebook_details").set_current_page(0)

    refresh = RefreshThread(treeview_update, statusIcon, wTree)
    refresh.start()

    auto_refresh = AutomaticRefreshThread(treeview_update, statusIcon, wTree)
    auto_refresh.start()
    gtk.main()

except Exception, detail:
    print detail
    log.writelines("-- Exception occured in main thread: " + str(detail) + "\n")
    log.flush()
    log.close()
