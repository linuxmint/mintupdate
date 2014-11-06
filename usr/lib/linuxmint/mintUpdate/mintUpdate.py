#!/usr/bin/env python

try:
    import os
    import commands
    import codecs
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
    import re
    from user import home
    from sets import Set
    import proxygsettings
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
        os.system("killall mintUpdate")        
except Exception, detail:
    print detail

architecture = commands.getoutput("uname -a")
if (architecture.find("x86_64") >= 0):
    import ctypes
    libc = ctypes.CDLL('libc.so.6')
    libc.prctl(15, 'mintUpdate', 0, 0, 0)
else:
    import dl
    if os.path.exists('/lib/libc.so.6'):
        libc = dl.open('/lib/libc.so.6')
        libc.call('prctl', 15, 'mintUpdate', 0, 0, 0)
    elif os.path.exists('/lib/i386-linux-gnu/libc.so.6'):
        libc = dl.open('/lib/i386-linux-gnu/libc.so.6')
        libc.call('prctl', 15, 'mintUpdate', 0, 0, 0)

# i18n
gettext.install("mintupdate", "/usr/share/linuxmint/locale")

CONFIG_DIR = "%s/.config/linuxmint" % home

package_short_descriptions = {}
package_descriptions = {}

(UPDATE_CHECKED, UPDATE_NAME, UPDATE_LEVEL_PIX, UPDATE_OLD_VERSION, UPDATE_NEW_VERSION, UPDATE_LEVEL_STR, UPDATE_SIZE, UPDATE_SIZE_STR, UPDATE_TYPE_PIX, UPDATE_TYPE, UPDATE_TOOLTIP, UPDATE_SORT_STR, UPDATE_OBJ) = range(13)

class PackageUpdate():
    def __init__(self, source_package_name, level, oldVersion, newVersion, extraInfo, warning, update_type, tooltip):
        self.name = source_package_name
        self.description = ""
        self.short_description = ""
        self.level = level
        self.oldVersion = oldVersion
        self.newVersion = newVersion
        self.size = 0
        self.extraInfo = extraInfo
        self.warning = warning
        self.type = update_type
        self.tooltip = tooltip
        self.packages = []

    def add_package(self, package, size, short_description, description):
        self.packages.append(package)
        self.description = description
        self.short_description = short_description
        self.size += size

class ChangelogRetriever(threading.Thread):
    def __init__(self, source_package, level, version, wTree):
        threading.Thread.__init__(self)
        self.source_package = source_package
        self.level = level 
        self.version = version
        self.wTree = wTree
        # get the proxy settings from gsettings
        self.ps = proxygsettings.get_proxy_settings()


        # Remove the epoch if present in the version
        if ":" in self.version:
            self.version = self.version.split(":")[-1]
           
    def run(self):         
        gtk.gdk.threads_enter()
        self.wTree.get_widget("textview_changes").get_buffer().set_text(_("Downloading changelog..."))  
        gtk.gdk.threads_leave()       
        
        changelog_sources = []            
        if (self.source_package.startswith("lib")):
            changelog_sources.append("http://changelogs.ubuntu.com/changelogs/pool/main/%s/%s/%s_%s/changelog" % (self.source_package[0:4], self.source_package, self.source_package, self.version))        
            changelog_sources.append("http://changelogs.ubuntu.com/changelogs/pool/multiverse/%s/%s/%s_%s/changelog" % (self.source_package[0:4], self.source_package, self.source_package, self.version))
            changelog_sources.append("http://changelogs.ubuntu.com/changelogs/pool/universe/%s/%s/%s_%s/changelog" % (self.source_package[0:4], self.source_package, self.source_package, self.version))        
            changelog_sources.append("http://changelogs.ubuntu.com/changelogs/pool/restricted/%s/%s/%s_%s/changelog" % (self.source_package[0:4], self.source_package, self.source_package, self.version))
        else:
            changelog_sources.append("http://changelogs.ubuntu.com/changelogs/pool/main/%s/%s/%s_%s/changelog" % (self.source_package[0], self.source_package, self.source_package, self.version))        
            changelog_sources.append("http://changelogs.ubuntu.com/changelogs/pool/multiverse/%s/%s/%s_%s/changelog" % (self.source_package[0], self.source_package, self.source_package, self.version))
            changelog_sources.append("http://changelogs.ubuntu.com/changelogs/pool/universe/%s/%s/%s_%s/changelog" % (self.source_package[0], self.source_package, self.source_package, self.version))        
            changelog_sources.append("http://changelogs.ubuntu.com/changelogs/pool/restricted/%s/%s/%s_%s/changelog" % (self.source_package[0], self.source_package, self.source_package, self.version))
        changelog_sources.append("http://packages.linuxmint.com/dev/" + self.source_package + "_" + self.version + "_amd64.changes")
        changelog_sources.append("http://packages.linuxmint.com/dev/" + self.source_package + "_" + self.version + "_i386.changes")
        
        changelog = _("No changelog available")
        
        if self.ps == {}:
            # use default urllib2 proxy mechanisms (possibly *_proxy environment vars)
            proxy = urllib2.ProxyHandler()
        else:
            # use proxy settings retrieved from gsettings
            proxy = urllib2.ProxyHandler(self.ps)

        opener = urllib2.build_opener(proxy)
        urllib2.install_opener(opener)

        for changelog_source in changelog_sources:
            try:                      
                print "Trying to fetch the changelog from: %s" % changelog_source
                url = urllib2.urlopen(changelog_source, None, 10)
                source = url.read()
                url.close()
                
                changelog = ""
                if "linuxmint.com" in changelog_source:
                    changes = source.split("\n")
                    for change in changes:
                        change = change.strip()
                        if change.startswith("*"):
                            changelog = changelog + change + "\n"
                else:
                    changelog = source                
                break
            except:
                pass
                                        
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
                        refresh = RefreshThread(self.treeView, self.statusIcon, self.wTree, root_mode=True)
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

class InstallKernelThread(threading.Thread): 

    def __init__(self, version, wTree, remove=False):
        threading.Thread.__init__(self)        
        self.version = version
        self.wTree = wTree
        self.remove = remove

    def run(self):
        cmd = ["pkexec", "/usr/sbin/synaptic", "--hide-main-window",  \
                "--non-interactive", "--parent-window-id", "%s" % self.wTree.get_widget("window5").window.xid]
        cmd.append("-o")
        cmd.append("Synaptic::closeZvt=true")
        cmd.append("--progress-str")
        cmd.append("\"" + _("Please wait, this can take some time") + "\"")
        cmd.append("--finish-str")
        if self.remove:
            cmd.append("\"" + _("The %s kernel was removed") % self.version + "\"")
        else:
            cmd.append("\"" + _("The %s kernel was installed") % self.version + "\"")
        f = tempfile.NamedTemporaryFile()

        for pkg in ['linux-headers-%s' % self.version, 'linux-headers-%s-generic' % self.version, 'linux-image-%s-generic' % self.version, 'linux-image-extra-%s-generic' % self.version]:
            if self.remove: 
                f.write("%s\tdeinstall\n" % pkg)
            else:
                f.write("%s\tinstall\n" % pkg)
        cmd.append("--set-selections-file")
        cmd.append("%s" % f.name)
        f.flush()        
        comnd = Popen(' '.join(cmd), stdout=log, stderr=log, shell=True)
        returnCode = comnd.wait()
        f.close()
        #sts = os.waitpid(comnd.pid, 0)                
        

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
            while (iter != None):
                checked = model.get_value(iter, UPDATE_CHECKED)
                if (checked == "true"):
                    installNeeded = True
                    package_update = model.get_value(iter, UPDATE_OBJ)
                    for package in package_update.packages:
                        packages.append(package)
                        log.writelines("++ Will install " + str(package) + "\n")
                        log.flush()
                iter = model.iter_next(iter)
            
            if (installNeeded == True):
                                
                proceed = True                
                try:
                    pkgs = ' '.join(str(pkg) for pkg in packages)
                    warnings = commands.getoutput("/usr/lib/linuxmint/mintUpdate/checkWarnings.py %s" % pkgs)
                    #print ("/usr/lib/linuxmint/mintUpdate/checkWarnings.py %s" % pkgs)
                    warnings = warnings.split("###")
                    if len(warnings) == 2:
                        installations = warnings[0].split()
                        removals = warnings[1].split()                                    
                        if len(installations) > 0 or len(removals) > 0:
                            gtk.gdk.threads_enter()
                            try:
                                dialog = gtk.MessageDialog(None, gtk.DIALOG_MODAL | gtk.DIALOG_DESTROY_WITH_PARENT, gtk.MESSAGE_WARNING, gtk.BUTTONS_OK_CANCEL, None)
                                dialog.set_title("")
                                dialog.set_markup("<b>" + _("This upgrade will trigger additional changes") + "</b>")
                                #dialog.format_secondary_markup("<i>" + _("All available upgrades for this package will be ignored.") + "</i>")                                
                                dialog.set_icon_from_file("/usr/lib/linuxmint/mintUpdate/icons/base.svg")
                                dialog.set_default_size(320, 400)
                                dialog.set_resizable(True)
                                
                                if len(removals) > 0:
                                    # Removals
                                    label = gtk.Label()
                                    if len(removals) == 1:
                                        label.set_text(_("The following package will be removed:"))
                                    else:
                                        label.set_text(_("The following %d packages will be removed:") % len(removals))                                    
                                    label.set_alignment(0, 0.5)
                                    scrolledWindow = gtk.ScrolledWindow()
                                    scrolledWindow.set_shadow_type(gtk.SHADOW_IN)
                                    scrolledWindow.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
                                    treeview = gtk.TreeView()
                                    column1 = gtk.TreeViewColumn("", gtk.CellRendererText(), text=0)
                                    column1.set_sort_column_id(0)
                                    column1.set_resizable(True)
                                    treeview.append_column(column1)
                                    treeview.set_headers_clickable(False)
                                    treeview.set_reorderable(False)
                                    treeview.set_headers_visible(False)                                                        
                                    model = gtk.TreeStore(str)
                                    removals.sort()
                                    for pkg in removals:
                                        iter = model.insert_before(None, None)                                
                                        model.set_value(iter, 0, pkg)
                                    treeview.set_model(model)
                                    treeview.show()
                                    scrolledWindow.add(treeview)                                
                                    dialog.vbox.pack_start(label, False, False, 0)
                                    dialog.vbox.pack_start(scrolledWindow, True, True, 0)
                                
                                if len(installations) > 0:
                                    # Installations
                                    label = gtk.Label()
                                    if len(installations) == 1:
                                        label.set_text(_("The following package will be installed:"))
                                    else:
                                        label.set_text(_("The following %d packages will be installed:") % len(installations))
                                    label.set_alignment(0, 0.5)
                                    scrolledWindow = gtk.ScrolledWindow()
                                    scrolledWindow.set_shadow_type(gtk.SHADOW_IN)
                                    scrolledWindow.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
                                    treeview = gtk.TreeView()
                                    column1 = gtk.TreeViewColumn("", gtk.CellRendererText(), text=0)
                                    column1.set_sort_column_id(0)
                                    column1.set_resizable(True)
                                    treeview.append_column(column1)
                                    treeview.set_headers_clickable(False)
                                    treeview.set_reorderable(False)
                                    treeview.set_headers_visible(False)                                                        
                                    model = gtk.TreeStore(str)
                                    installations.sort()
                                    for pkg in installations:
                                        iter = model.insert_before(None, None)                                
                                        model.set_value(iter, 0, pkg)                               
                                    treeview.set_model(model)
                                    treeview.show()
                                    scrolledWindow.add(treeview)   
                                    dialog.vbox.pack_start(label, False, False, 0)
                                    dialog.vbox.pack_start(scrolledWindow, True, True, 0)
                                
                                dialog.show_all()                
                                if dialog.run() == gtk.RESPONSE_OK:
                                    proceed = True
                                else: 
                                    proceed = False
                                dialog.destroy()  
                            except Exception, detail:
                                print detail
                            gtk.gdk.threads_leave()   
                        else:
                            proceed = True
                except Exception, details: 
                    print details
                                                                       
                if proceed:
                    gtk.gdk.threads_enter()
                    self.statusIcon.set_from_file(icon_apply)
                    self.statusIcon.set_tooltip(_("Installing updates"))
                    gtk.gdk.threads_leave()
                    log.writelines("++ Ready to launch synaptic\n")
                    log.flush()
                    cmd = ["pkexec", "/usr/sbin/synaptic", "--hide-main-window",  \
                            "--non-interactive", "--parent-window-id", "%s" % self.wTree.get_widget("window1").window.xid]
                    cmd.append("-o")
                    cmd.append("Synaptic::closeZvt=true")
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
                                       
                    if "mintupdate" in packages:
                        # Restart                        
                        try:
                            log.writelines("++ Mintupdate was updated, restarting it...\n")
                            log.flush()
                            log.close()
                        except:
                            pass #cause we might have closed it already
            
                        command = "/usr/lib/linuxmint/mintUpdate/mintUpdate.py show &"                        
                        os.system(command)
                    
                    else:
                        # Refresh
                        gtk.gdk.threads_enter()
                        self.statusIcon.set_from_file(icon_busy)
                        self.statusIcon.set_tooltip(_("Checking for updates"))
                        self.wTree.get_widget("window1").window.set_cursor(None)
                        self.wTree.get_widget("window1").set_sensitive(True)                        
                        gtk.gdk.threads_leave()
                        refresh = RefreshThread(self.treeView, self.statusIcon, self.wTree)
                        refresh.start()
                else:
                    # Stop the blinking but don't refresh
                    gtk.gdk.threads_enter()
                    self.wTree.get_widget("window1").window.set_cursor(None)
                    self.wTree.get_widget("window1").set_sensitive(True)
                    gtk.gdk.threads_leave()
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

    def __init__(self, treeview_update, statusIcon, wTree, root_mode=False):
        threading.Thread.__init__(self)
        self.treeview_update = treeview_update
        self.statusIcon = statusIcon
        self.wTree = wTree
        self.root_mode = root_mode
    
    def fetch_l10n_descriptions(self, package_names):
        if os.path.exists("/var/lib/apt/lists"):
            try:
                super_buffer = []
                for file in os.listdir("/var/lib/apt/lists"):
                    if ("i18n_Translation") in file and not file.endswith("Translation-en"):
                        fd = codecs.open(os.path.join("/var/lib/apt/lists", file), "r", "utf-8")                    
                        super_buffer += fd.readlines()

                i = 0
                while i < len(super_buffer):
                    line = super_buffer[i].strip()
                    if line.startswith("Package: "):
                        try:
                            pkgname = line.replace("Package: ", "")
                            short_description = ""
                            description = ""
                            j = 2 # skip md5 line after package name line
                            while True:
                                if (i+j >= len(super_buffer)):
                                    break
                                line = super_buffer[i+j].strip()
                                if line.startswith("Package: "):
                                    break
                                if j==2:
                                    short_description = line
                                else:
                                    description += "\n" + line
                                j += 1
                            if pkgname in package_names:
                                if not package_descriptions.has_key(pkgname):
                                    package_short_descriptions[pkgname] = short_description
                                    package_descriptions[pkgname] = description
                        except Exception, detail:
                            print "a %s" % detail
                    i += 1
                del super_buffer    
            except Exception, detail:
                print "Could not fetch l10n descriptions.."
                print detail

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

            model = gtk.TreeStore(str, str, gtk.gdk.Pixbuf, str, str, str, int, str, gtk.gdk.Pixbuf, str, str, str, object) 
            # UPDATE_CHECKED, UPDATE_NAME, UPDATE_LEVEL_PIX, UPDATE_OLD_VERSION, UPDATE_NEW_VERSION, UPDATE_LEVEL_STR, 
            # UPDATE_SIZE, UPDATE_SIZE_STR, UPDATE_TYPE_PIX, UPDATE_TYPE, UPDATE_TOOLTIP, UPDATE_SORT_STR, UPDATE_OBJ

            model.set_sort_column_id( UPDATE_SORT_STR, gtk.SORT_ASCENDING )

            prefs = read_configuration()

            # Check to see if no other APT process is running
            if self.root_mode:
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
                refresh_command = "/usr/lib/linuxmint/mintUpdate/checkAPT.py 2>/dev/null"
            else:
                refresh_command = "/usr/lib/linuxmint/mintUpdate/checkAPT.py --use-synaptic %s 2>/dev/null" % self.wTree.get_widget("window1").window.xid
            if self.root_mode:
                refresh_command = "sudo %s" % refresh_command
            updates =  commands.getoutput(refresh_command)

            # Look for mintupdate
            if ("UPDATE###mintupdate###" in updates):                
                new_mintupdate = True
            else:
                new_mintupdate = False
           
            updates = string.split(updates, "---EOL---")         

            # Look at the updates one by one
            package_updates = {}
            package_names = Set()
            num_visible = 0
            num_safe = 0            
            download_size = 0
            num_ignored = 0
            ignored_list = []
            if os.path.exists("%s/mintupdate.ignored" % CONFIG_DIR):
                blacklist_file = open("%s/mintupdate.ignored" % CONFIG_DIR, "r")
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
                    if len(values) == 9:
                        status = values[0]
                        if (status == "ERROR"):
                            try:
                                error_msg = updates[1]
                            except:
                                error_msg = ""

                            gtk.gdk.threads_enter()
                            self.statusIcon.set_from_file(icon_error)
                            self.statusIcon.set_tooltip("%s\n\n%s" % (_("Could not refresh the list of updates"), error_msg))
                            statusbar.push(context_id, _("Could not refresh the list of updates"))
                            log.writelines("-- Error in checkAPT.py, could not refresh the list of updates\n")
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
                        newVersion = values[2]
                        oldVersion = values[3]
                        size = int(values[4])
                        source_package = values[5]
                        update_type = values[6]
                        short_description = values[7]
                        description = values[8]

                        package_names.add(package.replace(":i386", "").replace(":amd64", ""))

                        if not package_updates.has_key(source_package):
                            updateIsBlacklisted = False
                            for blacklist in ignored_list:
                                if fnmatch.fnmatch(source_package, blacklist):
                                    num_ignored = num_ignored + 1
                                    updateIsBlacklisted = True
                                    break

                            if updateIsBlacklisted:
                                continue

                            is_a_mint_package = False
                            if (update_type == "linuxmint"):
                                update_type = "package"
                                is_a_mint_package = True

                            security_update = (update_type == "security")                        

                            if update_type == "security":
                                tooltip = _("Security update")
                            elif update_type == "backport":
                                tooltip = _("Software backport. Be careful when upgrading. New versions of sofware can introduce regressions.")
                            elif update_type == "unstable":
                                tooltip = _("Unstable software. Only apply this update to help developers beta-test new software.")
                            else:
                                tooltip = _("Software update")

                            extraInfo = ""
                            warning = ""
                            if is_a_mint_package:
                                level = 1
                            else:
                                level = 3 # Level 3 by default                            
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
                                            if (rule_package == source_package):
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
                                                    index = source_package.find(keyword)
                                                    if (index > -1 and foundPackageRule == False):
                                                        level = rule_level
                                                        extraInfo = rule_extraInfo
                                                        warning = rule_warning
                                rulesFile.close()
                                level = int(level)

                            # Create a new Update
                            update = PackageUpdate(source_package, level, oldVersion, newVersion, extraInfo, warning, update_type, tooltip)
                            update.add_package(package, size, short_description, description)
                            package_updates[source_package] = update
                        else:
                            # Add the package to the Update
                            update = package_updates[source_package]
                            update.add_package(package, size, short_description, description)
                        
                self.fetch_l10n_descriptions(package_names)

                for source_package in package_updates.keys():
                    
                    package_update = package_updates[source_package]

                    if (new_mintupdate and package_update.name != "mintupdate"):
                        continue

                    # l10n descriptions
                    l10n_descriptions(package_update)
                    package_update.short_description = clean_l10n_short_description(package_update.short_description)
                    package_update.description = clean_l10n_description(package_update.description)
                    
                    if ((prefs["level" + str(package_update.level) + "_visible"]) or (security_update and prefs['security_visible'])):
                        iter = model.insert_before(None, None)
                        if (security_update and prefs['security_visible'] and prefs['security_safe']):
                            model.set_value(iter, UPDATE_CHECKED, "true")                            
                            num_safe = num_safe + 1
                            download_size = download_size + package_update.size
                        elif (prefs["level" + str(level) + "_safe"]):
                            model.set_value(iter, UPDATE_CHECKED, "true")                     
                            num_safe = num_safe + 1
                            download_size = download_size + package_update.size
                        else:
                            model.set_value(iter, UPDATE_CHECKED, "false")
                                                                              
                        model.row_changed(model.get_path(iter), iter)

                        shortdesc = package_update.short_description
                        if len(shortdesc) > 100:
                            shortdesc = shortdesc[:100] + "..."
                        if (prefs["descriptions_visible"]):
                            model.set_value(iter, UPDATE_NAME, package_update.name + "\n<small><span foreground='#5C5C5C'>%s</span></small>" % shortdesc)
                        else:
                            model.set_value(iter, UPDATE_NAME, package_update.name)
                        model.set_value(iter, UPDATE_LEVEL_PIX, gtk.gdk.pixbuf_new_from_file("/usr/lib/linuxmint/mintUpdate/icons/level" + str(package_update.level) + ".png"))
                        model.set_value(iter, UPDATE_OLD_VERSION, package_update.oldVersion)                                
                        model.set_value(iter, UPDATE_NEW_VERSION, package_update.newVersion)                        
                        model.set_value(iter, UPDATE_LEVEL_STR, str(package_update.level))
                        model.set_value(iter, UPDATE_SIZE, package_update.size)
                        model.set_value(iter, UPDATE_SIZE_STR, size_to_string(package_update.size))                        
                        model.set_value(iter, UPDATE_TYPE_PIX, gtk.gdk.pixbuf_new_from_file("/usr/lib/linuxmint/mintUpdate/icons/update-type-%s.png" % package_update.type))
                        model.set_value(iter, UPDATE_TYPE, package_update.type)
                        model.set_value(iter, UPDATE_TOOLTIP, package_update.tooltip)
                        model.set_value(iter, UPDATE_SORT_STR, "%s%s" % (str(package_update.level), package_update.name))
                        model.set_value(iter, UPDATE_OBJ, package_update)
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
            self.statusIcon.set_tooltip(_("Could not refresh the list of updates"))
            #self.statusIcon.set_blinking(False)
            self.wTree.get_widget("window1").window.set_cursor(None)
            self.wTree.get_widget("window1").set_sensitive(True)
            statusbar.push(context_id, _("Could not refresh the list of updates"))
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
    refresh = RefreshThread(treeview, statusIcon, wTree, root_mode=True)
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
        model.set_value(iter, UPDATE_CHECKED, "true")
        iter = model.iter_next(iter)
    iter = model.get_iter_first()
    download_size = 0
    num_selected = 0
    while (iter != None):
        checked = model.get_value(iter, UPDATE_CHECKED)
        if (checked == "true"):            
            size = model.get_value(iter, UPDATE_SIZE)
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

    config = ConfigObj("%s/mintUpdate.conf" % CONFIG_DIR)

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
    config['levels']['security_visible'] = prefs_tree.get_widget("checkbutton_security_visible").get_active()
    config['levels']['security_safe'] = prefs_tree.get_widget("checkbutton_security_safe").get_active()

    #Write refresh config
    config['refresh'] = {}
    config['refresh']['timer_minutes'] = int(prefs_tree.get_widget("timer_minutes").get_value())
    config['refresh']['timer_hours'] = int(prefs_tree.get_widget("timer_hours").get_value())
    config['refresh']['timer_days'] = int(prefs_tree.get_widget("timer_days").get_value())

    #Write update config
    config['update'] = {}    
    config['update']['dist_upgrade'] = prefs_tree.get_widget("checkbutton_dist_upgrade").get_active()

    #Write icons config
    config['icons'] = {}
    config['icons']['busy'] = icon_busy
    config['icons']['up2date'] = icon_up2date
    config['icons']['updates'] = icon_updates
    config['icons']['error'] = icon_error
    config['icons']['unknown'] = icon_unknown
    config['icons']['apply'] = icon_apply
    
    #Write blacklisted updates
    ignored_list = open("%s/mintupdate.ignored" % CONFIG_DIR, "w")
    treeview_blacklist = prefs_tree.get_widget("treeview_blacklist")
    model = treeview_blacklist.get_model()
    iter = model.get_iter_first()
    while iter is not None:
        pkg = model.get_value(iter, UPDATE_CHECKED)
        iter = model.iter_next(iter)
        ignored_list.writelines(pkg + "\n")
    ignored_list.close()

    config.write()

    prefs_tree.get_widget("window2").hide()
    refresh = RefreshThread(treeview, statusIcon, wTree)
    refresh.start()

def kernels_cancel(widget, tree):
    tree.get_widget("window5").hide()

def info_cancel(widget, prefs_tree):
    prefs_tree.get_widget("window3").hide()

def history_cancel(widget, tree):
    tree.get_widget("window4").hide()

def pref_cancel(widget, prefs_tree):
    prefs_tree.get_widget("window2").hide()

def read_configuration():
    global icon_busy
    global icon_up2date
    global icon_updates
    global icon_error
    global icon_unknown
    global icon_apply

    config = ConfigObj("%s/mintUpdate.conf" % CONFIG_DIR)
    prefs = {}

    #Read refresh config
    try:
        prefs["timer_minutes"] = int(config['refresh']['timer_minutes'])
        prefs["timer_hours"] = int(config['refresh']['timer_hours'])
        prefs["timer_days"] = int(config['refresh']['timer_days'])
    except:
        prefs["timer_minutes"] = 30
        prefs["timer_hours"] = 0
        prefs["timer_days"] = 0

    #Read update config
    try:        
        prefs["dist_upgrade"] = (config['update']['dist_upgrade'] == "True")
    except:        
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
        prefs["security_visible"] = (config['levels']['security_visible'] == "True")
        prefs["security_safe"] = (config['levels']['security_safe'] == "True")
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
        prefs["security_visible"] = True
        prefs["security_safe"] = False

    #Read columns config
    try:
        prefs["type_column_visible"] = (config['visible_columns']['type'] == "True")
    except:
        prefs["type_column_visible"] = True
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
        prefs["old_version_column_visible"] = False
    try:
        prefs["new_version_column_visible"] = (config['visible_columns']['new_version'] == "True")
    except:
        prefs["new_version_column_visible"] = True
    try:
        prefs["size_column_visible"] = (config['visible_columns']['size'] == "True")
    except:
        prefs["size_column_visible"] = False
    try:
        prefs["descriptions_visible"] = (config['visible_columns']['description'] == "True")
    except:
        prefs["descriptions_visible"] = True

    #Read window dimensions
    try:
        prefs["dimensions_x"] = int(config['dimensions']['x'])
        prefs["dimensions_y"] = int(config['dimensions']['y'])
        prefs["dimensions_pane_position"] = int(config['dimensions']['pane_position'])
    except:
        prefs["dimensions_x"] = 790
        prefs["dimensions_y"] = 540
        prefs["dimensions_pane_position"] = 278

    #Read package blacklist
    try:
        prefs["blacklisted_packages"] = config['blacklisted_packages']
    except:
        prefs["blacklisted_packages"] = []

    return prefs

def open_repositories(widget):
    if os.path.exists("/usr/bin/software-sources"):
        os.system("/usr/bin/software-sources &")
    elif os.path.exists("/usr/bin/software-properties-gtk"):
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
    prefs_tree.get_widget("label43").set_text(_("Certified updates. Tested through Romeo or directly maintained by Linux Mint."))
    prefs_tree.get_widget("label44").set_text(_("Recommended updates. Tested and approved by Linux Mint."))
    prefs_tree.get_widget("label45").set_text(_("Safe updates. Not tested but believed to be safe."))
    prefs_tree.get_widget("label46").set_text(_("Unsafe updates. Could potentially affect the stability of the system."))
    prefs_tree.get_widget("label47").set_text(_("Dangerous updates. Known to affect the stability of the systems depending on certain specs or hardware."))
    prefs_tree.get_widget("label55").set_text(_("Linux Mint"))
    prefs_tree.get_widget("label56").set_text(_("Upstream"))
    prefs_tree.get_widget("label57").set_text(_("Upstream"))
    prefs_tree.get_widget("label58").set_text(_("Upstream"))
    prefs_tree.get_widget("label59").set_text(_("Upstream"))
    prefs_tree.get_widget("label81").set_text(_("Refresh the list of updates every:"))
    prefs_tree.get_widget("label82").set_text("<i>" + _("Note: The list only gets refreshed while the update manager window is closed (system tray mode).") + "</i>")
    prefs_tree.get_widget("label82").set_use_markup(True)
    prefs_tree.get_widget("label83").set_text(_("Update Method"))        
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
    prefs_tree.get_widget("label1").set_text(_("Ignored updates"))

    prefs_tree.get_widget("checkbutton_dist_upgrade").set_label(_("Include updates which require the installation of new packages or the removal of installed packages"))

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
    prefs_tree.get_widget("checkbutton_security_visible").set_active(prefs["security_visible"])
    prefs_tree.get_widget("checkbutton_security_safe").set_active(prefs["security_safe"])    

    prefs_tree.get_widget("checkbutton_security_visible").set_label(_("Always show security updates"))
    prefs_tree.get_widget("checkbutton_security_safe").set_label(_("Always select and trust security updates"))

    prefs_tree.get_widget("timer_minutes_label").set_text(_("minutes"))
    prefs_tree.get_widget("timer_hours_label").set_text(_("hours"))
    prefs_tree.get_widget("timer_days_label").set_text(_("days"))
    prefs_tree.get_widget("timer_minutes").set_value(prefs["timer_minutes"])
    prefs_tree.get_widget("timer_hours").set_value(prefs["timer_hours"])
    prefs_tree.get_widget("timer_days").set_value(prefs["timer_days"])

    prefs_tree.get_widget("checkbutton_dist_upgrade").set_active(prefs["dist_upgrade"])

    prefs_tree.get_widget("image_busy").set_from_pixbuf(gtk.gdk.pixbuf_new_from_file_at_size(icon_busy, 24, 24))
    prefs_tree.get_widget("image_up2date").set_from_pixbuf(gtk.gdk.pixbuf_new_from_file_at_size(icon_up2date, 24, 24))
    prefs_tree.get_widget("image_updates").set_from_pixbuf(gtk.gdk.pixbuf_new_from_file_at_size(icon_updates, 24, 24))
    prefs_tree.get_widget("image_error").set_from_pixbuf(gtk.gdk.pixbuf_new_from_file_at_size(icon_error, 24, 24))
    prefs_tree.get_widget("image_unknown").set_from_pixbuf(gtk.gdk.pixbuf_new_from_file_at_size(icon_unknown, 24, 24))
    prefs_tree.get_widget("image_apply").set_from_pixbuf(gtk.gdk.pixbuf_new_from_file_at_size(icon_apply, 24, 24))

    # Blacklisted updates
    treeview_blacklist = prefs_tree.get_widget("treeview_blacklist")
    column1 = gtk.TreeViewColumn(_("Ignored updates"), gtk.CellRendererText(), text=0)
    column1.set_sort_column_id(0)
    column1.set_resizable(True)
    treeview_blacklist.append_column(column1)
    treeview_blacklist.set_headers_clickable(True)
    treeview_blacklist.set_reorderable(False)
    treeview_blacklist.show()

    model = gtk.TreeStore(str)
    model.set_sort_column_id( 0, gtk.SORT_ASCENDING )
    treeview_blacklist.set_model(model)

    if os.path.exists("%s/mintupdate.ignored" % CONFIG_DIR):
        ignored_list = open("%s/mintupdate.ignored" % CONFIG_DIR, "r")
        for ignored_pkg in ignored_list:            
            iter = model.insert_before(None, None)
            model.set_value(iter, 0, ignored_pkg.strip())
        del model
        ignored_list.close()
    
    prefs_tree.get_widget("toolbutton_add").connect("clicked", add_blacklisted_package, treeview_blacklist)
    prefs_tree.get_widget("toolbutton_remove").connect("clicked", remove_blacklisted_package, treeview_blacklist)

def add_blacklisted_package(widget, treeview_blacklist):

    dialog = gtk.MessageDialog(None, gtk.DIALOG_MODAL | gtk.DIALOG_DESTROY_WITH_PARENT, gtk.MESSAGE_QUESTION, gtk.BUTTONS_OK, None)
    dialog.set_markup("<b>" + _("Please specify the name of the update to ignore:") + "</b>")
    dialog.set_title(_("Ignore an update"))
    dialog.set_icon_from_file("/usr/lib/linuxmint/mintUpdate/icons/base.svg")
    entry = gtk.Entry()
    hbox = gtk.HBox()
    hbox.pack_start(gtk.Label(_("Name:")), False, 5, 5)
    hbox.pack_end(entry)    
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
        pkg = model.get_value(iter, UPDATE_CHECKED)
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
    column2 = gtk.TreeViewColumn(_("Package"), gtk.CellRendererText(), text=0)
    column2.set_sort_column_id(0)
    column2.set_resizable(True)
    column3 = gtk.TreeViewColumn(_("Old version"), gtk.CellRendererText(), text=2)
    column3.set_sort_column_id(2)
    column3.set_resizable(True)
    column4 = gtk.TreeViewColumn(_("New version"), gtk.CellRendererText(), text=3)
    column4.set_sort_column_id(3)
    column4.set_resizable(True)    

    treeview_update.append_column(column1)
    treeview_update.append_column(column2)
    treeview_update.append_column(column3)    
    treeview_update.append_column(column4)    

    treeview_update.set_headers_clickable(True)
    treeview_update.set_reorderable(False)
    treeview_update.set_search_column(0)
    treeview_update.set_enable_search(True)
    treeview_update.show()

    model = gtk.TreeStore(str, str, str, str) # (packageName, date, oldVersion, newVersion)

    if (os.path.exists("/var/log/dpkg.log")):
        updates = commands.getoutput("cat /var/log/dpkg.log /var/log/dpkg.log.? 2>/dev/null | egrep \"upgrade\"")
        updates = string.split(updates, "\n")
        for pkg in updates:
            values = string.split(pkg, " ")
            if len(values) == 6:
                date = values[0]
                time = values[1]
                action = values[2]
                package = values[3]
                oldVersion = values[4]
                newVersion = values[5]    

                if action != "upgrade":
                    continue

                if oldVersion == newVersion:
                    continue

                if ":" in package:
                    package = package.split(":")[0]

                iter = model.insert_before(None, None)
                model.set_value(iter, 0, package)
                model.row_changed(model.get_path(iter), iter)
                model.set_value(iter, 1, "%s - %s" % (date, time))                         
                model.set_value(iter, 2, oldVersion)
                model.set_value(iter, 3, newVersion)                

    model.set_sort_column_id( 1, gtk.SORT_DESCENDING )
    treeview_update.set_model(model)
    del model
    wTree.get_widget("button_close").connect("clicked", history_cancel, wTree)

def open_information(widget):
    global logFile
    global pid

    gladefile = "/usr/lib/linuxmint/mintUpdate/mintUpdate.glade"
    prefs_tree = gtk.glade.XML(gladefile, "window3")
    prefs_tree.get_widget("window3").set_title(_("Information") + " - " + _("Update Manager"))
    prefs_tree.get_widget("window3").set_icon_from_file("/usr/lib/linuxmint/mintUpdate/icons/base.svg")
    prefs_tree.get_widget("close_button").connect("clicked", info_cancel, prefs_tree)    
    prefs_tree.get_widget("label4").set_text(_("Process ID:"))
    prefs_tree.get_widget("label5").set_text(_("Log file:"))
    prefs_tree.get_widget("processid_label").set_text(str(pid))
    prefs_tree.get_widget("log_filename").set_text(str(logFile))
    txtbuffer = gtk.TextBuffer()
    txtbuffer.set_text(commands.getoutput("cat " + logFile))
    prefs_tree.get_widget("log_textview").set_buffer(txtbuffer)

def label_size_allocate(widget, rect):
    widget.set_size_request(rect.width, -1)

def install_kernel(widget, selection, wTree, window):
    (model, iter) = selection.get_selected()
    if (iter != None):
        (status, version, pkg_version, installed, used, recommended, installable) = model.get_value(iter, 7)
        installed = (installed == "1")
        used = (used == "1")            
        installable = (installable == "1")
        if (installed):
            message = _("Are you sure you want to remove the %s kernel?") % version
        else:
            message = _("Are you sure you want to install the %s kernel?") % version
        image = gtk.Image()
        image.set_from_file("/usr/lib/linuxmint/mintUpdate/icons/warning.png")
        d = gtk.MessageDialog(window, gtk.DIALOG_MODAL | gtk.DIALOG_DESTROY_WITH_PARENT, gtk.MESSAGE_INFO, gtk.BUTTONS_YES_NO, message) 
        image.show()
        d.set_image(image)    
        d.set_default_response(gtk.RESPONSE_NO)
        r = d.run()   
        d.hide()     
        d.destroy()
        if r == gtk.RESPONSE_YES:
            thread = InstallKernelThread(version, wTree, installed)
            thread.start()        
            window.hide()

def open_kernels(widget):
    global logFile
    global pid

    gladefile = "/usr/lib/linuxmint/mintUpdate/mintUpdate.glade"
    tree = gtk.glade.XML(gladefile, "window5")
    window = tree.get_widget("window5")
    window.set_title(_("Linux kernels") + " - " + _("Update Manager"))
    window.set_icon_from_file("/usr/lib/linuxmint/mintUpdate/icons/base.svg")    
    tree.get_widget("close_button").connect("clicked", kernels_cancel, tree)

    tree.get_widget("label_warning").connect("size-allocate", label_size_allocate)
    tree.get_widget("label_contact").connect("size-allocate", label_size_allocate)


    tree.get_widget("title_warning").set_markup("<span foreground='black' font_weight='bold' size='large'>%s</span>" % _("Warning!"))
    tree.get_widget("label_warning").set_markup(_("The Linux kernel is a critical part of the system. Regressions can lead to lack of networking, lack of sound, lack of graphical environment or even the inability to boot the computer. Only install or remove kernels if you're experienced with kernels, drivers, dkms and you know how to recover a non-booting computer."))
    tree.get_widget("label_available").set_markup("%s" % _("The following kernels are available:"))    
    tree.get_widget("label_more_info").set_text(_("More info..."))

    tree.get_widget("label_more_info_1").set_markup("<small>%s</small>" % _("Fixes can represent bug fixes, improvements in hardware support or security fixes."))
    tree.get_widget("label_more_info_2").set_markup("<small>%s</small>" % _("Security fixes are important when local users represent a potential threat (in companies, libraries, schools or public places for instance) or when the computer can be threatened by remote attacks (servers for instance)."))
    tree.get_widget("label_more_info_3").set_markup("<small>%s</small>" % _("Bug fixes and hardware improvements are important if one of your devices isn't working as expected and the newer kernel addresses that problem."))
    tree.get_widget("label_more_info_4").set_markup("<small>%s</small>" % _("Regressions represent something which worked well and no longer works after an update. It is common in software development that a code change or even a bug fix introduces side effects and breaks something else. Because of regressions it is recommended to be selective when installing updates or newer kernels."))

    tree.get_widget("label_known_fixes").set_text(_("Fixes"))
    tree.get_widget("label_known_regressions").set_text(_("Regressions"))
    tree.get_widget("label_contact").set_markup("<span foreground='#3c3c3c' font_weight='bold' size='small'>%s</span>" % _("Note: Only known fixes and regressions are mentioned. If you are aware of additional fixes or regressions, please contact the development team."))

    # the treeview
    treeview_kernels = tree.get_widget("treeview_kernels")

    column1 = gtk.TreeViewColumn(_("Version"), gtk.CellRendererText(), markup=1)
    column1.set_sort_column_id(1)
    column1.set_resizable(True)
    column1.set_expand(True)
    column2 = gtk.TreeViewColumn(_("Loaded"), gtk.CellRendererPixbuf(), pixbuf=2)
    column2.set_sort_column_id(2)
    column2.set_resizable(True)
    column2.set_expand(False)
    column3 = gtk.TreeViewColumn(_("Recommended"), gtk.CellRendererPixbuf(), pixbuf=3)
    column3.set_sort_column_id(3)
    column3.set_resizable(True)
    column3.set_expand(False)
    column4 = gtk.TreeViewColumn(_("Installed"), gtk.CellRendererPixbuf(), pixbuf=4)
    column4.set_sort_column_id(4)
    column4.set_resizable(True)
    column4.set_expand(False)
    column5 = gtk.TreeViewColumn(_("Fixes"), gtk.CellRendererPixbuf(), pixbuf=5)
    column5.set_sort_column_id(5)
    column5.set_resizable(True)
    column5.set_expand(False)
    column6 = gtk.TreeViewColumn(_("Regressions"), gtk.CellRendererPixbuf(), pixbuf=6)
    column6.set_sort_column_id(6)
    column6.set_resizable(True)
    column6.set_expand(False)

    treeview_kernels.append_column(column1)
    treeview_kernels.append_column(column2)
    treeview_kernels.append_column(column3)
    treeview_kernels.append_column(column4)
    treeview_kernels.append_column(column5)
    treeview_kernels.append_column(column6)

    treeview_kernels.set_headers_clickable(True)
    treeview_kernels.set_reorderable(False)
    treeview_kernels.set_search_column(1)
    treeview_kernels.set_enable_search(True)
    treeview_kernels.show()

    model = gtk.TreeStore(str, str, gtk.gdk.Pixbuf, gtk.gdk.Pixbuf, gtk.gdk.Pixbuf, gtk.gdk.Pixbuf, gtk.gdk.Pixbuf, object) # (version, label, loaded, recommended, installed, fixes, regressions, values)

    kernels = commands.getoutput("/usr/lib/linuxmint/mintUpdate/checkKernels.py | grep \"###\"")
    kernels = kernels.split("\n")
    column = 2
    for kernel in kernels:
        values = string.split(kernel, "###")
        if len(values) == 7:
            status = values[0]
            if status != "KERNEL":
                continue
            (status, version, pkg_version, installed, used, recommended, installable) = values
            installed = (installed == "1")
            used = (used == "1")
            recommended = (recommended == "1")
            installable = (installable == "1")
            label = version

            tick = gtk.gdk.pixbuf_new_from_file("/usr/lib/linuxmint/mintUpdate/icons/tick.png")
            pix_fixes = gtk.gdk.pixbuf_new_from_file("/usr/lib/linuxmint/mintUpdate/icons/fixes.png")
            pix_bugs = gtk.gdk.pixbuf_new_from_file("/usr/lib/linuxmint/mintUpdate/icons/regressions.png")


            iter = model.insert_before(None, None)
            model.set_value(iter, 0, version)            
            model.set_value(iter, 7, values)
            model.row_changed(model.get_path(iter), iter)          
                        
            if os.path.exists("/usr/lib/linuxmint/mintUpdate/kernels/%s" % version):
                kernel_file = open("/usr/lib/linuxmint/mintUpdate/kernels/%s" % version)
                lines = kernel_file.readlines()
                num_fixes = 0
                num_bugs = 0
                for line in lines:
                    elements = line.split("---")
                    if len(elements) == 4:
                        (prefix, title, url, description) = elements                        
                        if prefix == "fix":
                            num_fixes += 1
                        elif prefix == "bug":
                            num_bugs += 1
                if num_fixes > 0:
                    model.set_value(iter, 5, pix_fixes)
                if num_bugs > 0:
                    model.set_value(iter, 6, pix_bugs)

            if os.path.exists("/usr/lib/linuxmint/mintUpdate/kernels/versions"):
                kernel_file = open("/usr/lib/linuxmint/mintUpdate/kernels/versions")
                lines = kernel_file.readlines()
                for line in lines:
                    elements = line.split("\t")
                    if len(elements) == 3:                        
                        (versions_version, versions_tag, versions_upstream) = elements                        
                        if versions_version == pkg_version:
                            label = "%s (%s)" % (version, versions_upstream.strip())                           

            if installable and not installed:
                button = gtk.Button(_("Install"))
                button.connect("clicked", install_kernel, version, window, tree, False)                

            elif installed and not used:                                            
                button = gtk.Button(_("Remove"))
                button.connect("clicked", install_kernel, version, window, tree, True)

            if used:                
                model.set_value(iter, 2, tick)
                label = "<b>%s</b>" % label                
            if recommended:                
                model.set_value(iter, 3, tick)
            if installed:                
                model.set_value(iter, 4, tick)

            model.set_value(iter, 1, label)
                
    treeview_kernels.set_model(model)
    del model

    selection = treeview_kernels.get_selection()
    selection.connect("changed", display_selected_kernel, tree)

    button_install = tree.get_widget("button_install")
    button_install.connect('clicked', install_kernel, selection, tree, window)

    window.show_all()

def display_selected_kernel(selection, wTree):
    button_install = wTree.get_widget("button_install")
    button_install.set_sensitive(False)
    button_install.set_tooltip_text("")
    try:
        scrolled_fixes = wTree.get_widget("scrolled_fixes")
        scrolled_regressions = wTree.get_widget("scrolled_regressions")
        for child in scrolled_fixes.get_children():
            scrolled_fixes.remove(child)
        for child in scrolled_regressions.get_children():
            scrolled_regressions.remove(child)        
        (model, iter) = selection.get_selected()
        if (iter != None):
            (status, version, pkg_version, installed, used, recommended, installable) = model.get_value(iter, 7)
            installed = (installed == "1")
            used = (used == "1")            
            installable = (installable == "1")
            if installed:
                button_install.set_label(_("Remove the %s kernel") % version)
                if used:
                    button_install.set_tooltip_text(_("This kernel cannot be removed because it is currently in use."))
                else:
                    button_install.set_sensitive(True)
            else:
                button_install.set_label(_("Install the %s kernel" % version))
                if not installable:
                    button_install.set_tooltip_text(_("This kernel is not installable."))
                else:
                    button_install.set_sensitive(True)
            if os.path.exists("/usr/lib/linuxmint/mintUpdate/kernels/%s" % version):
                kernel_file = open("/usr/lib/linuxmint/mintUpdate/kernels/%s" % version)
                lines = kernel_file.readlines()
                fixes_box = gtk.Table()
                fixes_box.set_row_spacings(3)
                bugs_box = gtk.Table()
                bugs_box.set_row_spacings(3)
                num_fixes = 0
                num_bugs = 0
                for line in lines:
                    elements = line.split("---")
                    if len(elements) == 4:
                        (prefix, title, url, description) = elements
                        link = gtk.Label()
                        link.set_markup("<a href='%s'>%s</a>" % (url.strip(), title.strip()))
                        link.set_alignment(0, 0.5);
                        description_label = gtk.Label()
                        description = description.strip()
                        description = re.sub(r'CVE-(\d+)-(\d+)', r'<a href="http://cve.mitre.org/cgi-bin/cvename.cgi?name=\g<0>">\g<0></a>', description)
                        description_label.set_markup("%s" % description.strip())
                        description_label.set_alignment(0, 0.5);
                        if prefix == "fix":
                            fixes_box.attach(link, 0, 1, num_fixes, num_fixes+1, xoptions=gtk.FILL, yoptions=gtk.FILL, xpadding=3, ypadding=0)
                            fixes_box.attach(description_label, 1, 2, num_fixes, num_fixes+1, xoptions=gtk.FILL, yoptions=gtk.FILL, xpadding=0, ypadding=0)
                            num_fixes += 1
                        elif prefix == "bug":
                            bugs_box.attach(link, 0, 1, num_bugs, num_bugs+1, xoptions=gtk.FILL, yoptions=gtk.FILL, xpadding=3, ypadding=0)
                            bugs_box.attach(description_label, 1, 2, num_bugs, num_bugs+1, xoptions=gtk.FILL, yoptions=gtk.FILL, xpadding=0, ypadding=0)
                            num_bugs += 1
                scrolled_fixes.add_with_viewport(fixes_box)
                scrolled_regressions.add_with_viewport(bugs_box)
                fixes_box.show_all()
                bugs_box.show_all()
    except Exception, detail:
        print detail           

def open_help(widget):
    os.system("yelp help:linuxmint/software-updates &")

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

def popup_menu_cb(widget, button, time, data = None):
    if button == 3:
        if data:
            data.show_all()
            data.popup(None, None, gtk.status_icon_position_menu, 3, time, widget)
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

def activate_icon_cb(widget, data, wTree):
    global app_hidden
    if (app_hidden == True):            
        wTree.get_widget("window1").show_all()
        app_hidden = False
    else:
        wTree.get_widget("window1").hide()
        app_hidden = True
        save_window_size(wTree.get_widget("window1"), wTree.get_widget("vpaned1"))

def save_window_size(window, vpaned):

    config = ConfigObj("%s/mintUpdate.conf" % CONFIG_DIR)
    config['dimensions'] = {}
    config['dimensions']['x'] = window.get_size()[0]
    config['dimensions']['y'] = window.get_size()[1]
    config['dimensions']['pane_position'] = vpaned.get_position()
    config.write()

def clean_l10n_short_description(description):
        try:            
            # Remove "Description-xx: " prefix
            value = re.sub(r'Description-(\S+): ', r'', description)
            # Only take the first line and trim it
            value = value.split("\n")[0].strip()
            # Capitalize the first letter
            value = value[:1].upper() + value[1:]
            # Add missing punctuation
            if len(value) > 0 and value[-1] not in [".", "!", "?"]:
                value = "%s." % value

            return value
        except Exception, detail:
            print detail
            return description

def clean_l10n_description(description):
        try:
            lines = description.split("\n")
            value = ""
            num = 0
            newline = False
            for line in lines:
                line = line.strip()
                if len(line) > 0:
                    if line == ".":
                        value = "%s\n" % (value)
                        newline = True
                    else:
                        if (newline):
                            value = "%s%s" % (value, line.capitalize())
                        else:
                            value = "%s %s" % (value, line)
                        newline = False
                    num += 1
            value = value.replace("  ", " ").strip()
            # Capitalize the first letter
            value = value[:1].upper() + value[1:]
            # Add missing punctuation
            if len(value) > 0 and value[-1] not in [".", "!", "?"]:
                value = "%s." % value
            return value
        except Exception, detail:
            print detail
            return description

def l10n_descriptions(package_update):
        package_name = package_update.name.replace(":i386", "").replace(":amd64", "")
        if package_descriptions.has_key(package_name):
            package_update.short_description = package_short_descriptions[package_name]
            package_update.description = package_descriptions[package_name]

def display_selected_package(selection, wTree):    
    try:
        wTree.get_widget("textview_description").get_buffer().set_text("")
        wTree.get_widget("textview_changes").get_buffer().set_text("")            
        (model, iter) = selection.get_selected()
        if (iter != None):                            
            package_update = model.get_value(iter, UPDATE_OBJ)
            if wTree.get_widget("notebook_details").get_current_page() == 0:
                # Description tab           
                description = package_update.description
                buffer = wTree.get_widget("textview_description").get_buffer()
                buffer.set_text(description)
                import pango
                try:
                    buffer.create_tag("dimmed", scale=pango.SCALE_SMALL, foreground="#5C5C5C", style=pango.STYLE_ITALIC)
                except:
                    # Already exists, no big deal..
                    pass
                if (len(package_update.packages) > 1):
                    dimmed_description = "\n%s %s" % (_("This update contains %d packages: ") % len(package_update.packages), " ".join(package_update.packages))
                    buffer.insert_with_tags_by_name(buffer.get_end_iter(), dimmed_description, "dimmed")
                elif (package_update.packages[0] != package_update.name):
                    dimmed_description = "\n%s %s" % (_("This update contains 1 package: "), package_update.packages[0])
                    buffer.insert_with_tags_by_name(buffer.get_end_iter(), dimmed_description, "dimmed")
            else:
                # Changelog tab
                retriever = ChangelogRetriever(package_update.name, package_update.level, package_update.newVersion, wTree)
                retriever.start()
               
    except Exception, detail:
        print detail

def switch_page(notebook, page, page_num, Wtree, treeView):
    selection = treeView.get_selection()
    (model, iter) = selection.get_selected()
    if (iter != None):
        package_update = model.get_value(iter, UPDATE_OBJ)
        if (page_num == 0):
            # Description tab
            description = package_update.description
            buffer = wTree.get_widget("textview_description").get_buffer()
            buffer.set_text(description)
            import pango
            try:
                buffer.create_tag("dimmed", scale=pango.SCALE_SMALL, foreground="#5C5C5C", style=pango.STYLE_ITALIC)
            except:
                # Already exists, no big deal..
                pass
            if (len(package_update.packages) > 1):
                dimmed_description = "\n%s %s" % (_("This update contains %d packages: ") % len(package_update.packages), " ".join(package_update.packages))
                buffer.insert_with_tags_by_name(buffer.get_end_iter(), dimmed_description, "dimmed")
            elif (package_update.packages[0] != package_update.name):
                dimmed_description = "\n%s %s" % (_("This update contains 1 package: "), package_update.packages[0])
                buffer.insert_with_tags_by_name(buffer.get_end_iter(), dimmed_description, "dimmed")
        else:
            # Changelog tab                
            retriever = ChangelogRetriever(package_update.name, package_update.level, package_update.newVersion, wTree)
            retriever.start()

def celldatafunction_checkbox(column, cell, model, iter):
    cell.set_property("activatable", True)
    checked = model.get_value(iter, UPDATE_CHECKED)
    if (checked == "true"):
        cell.set_property("active", True)
    else:
        cell.set_property("active", False)

def toggled(renderer, path, treeview, statusbar, context_id):
    model = treeview.get_model()
    iter = model.get_iter(path)
    if (iter != None):
        checked = model.get_value(iter, UPDATE_CHECKED)
        if (checked == "true"):
            model.set_value(iter, UPDATE_CHECKED, "false")
        else:
            model.set_value(iter, UPDATE_CHECKED, "true")
    
    iter = model.get_iter_first()
    download_size = 0
    num_selected = 0
    while (iter != None):
        checked = model.get_value(iter, UPDATE_CHECKED)
        if (checked == "true"):            
            size = model.get_value(iter, UPDATE_SIZE)
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
    config = ConfigObj("%s/mintUpdate.conf" % CONFIG_DIR)
    if (config.has_key('visible_columns')):
        config['visible_columns'][configName] = checkmenuitem.get_active()
    else:
        config['visible_columns'] = {}
        config['visible_columns'][configName] = checkmenuitem.get_active()
    config.write()
    column.set_visible(checkmenuitem.get_active())

def setVisibleDescriptions(checkmenuitem, treeView, statusIcon, wTree, prefs):
    config = ConfigObj("%s/mintUpdate.conf" % CONFIG_DIR)
    if (not config.has_key('visible_columns')):
        config['visible_columns'] = {}
    config['visible_columns']['description'] = checkmenuitem.get_active()
    config.write()
    prefs["descriptions_visible"] = checkmenuitem.get_active()
    refresh = RefreshThread(treeView, statusIcon, wTree)
    refresh.start()
    
def menuPopup(widget, event, treeview_update, statusIcon, wTree):
    if event.button == 3:
        (model, iter) = widget.get_selection().get_selected()
        if (iter != None):
            package_update = model.get_value(iter, UPDATE_OBJ)
            menu = gtk.Menu()                
            menuItem = gtk.MenuItem(_("Ignore updates for this package"))
            menuItem.connect("activate", add_to_ignore_list, treeview_update, package_update.name, statusIcon, wTree)
            menu.append(menuItem)        
            menu.show_all()        
            menu.popup( None, None, None, 3, 0)
        
def add_to_ignore_list(widget, treeview_update, pkg, statusIcon, wTree):
    os.system("echo \"%s\" >> %s/mintupdate.ignored" % (pkg, CONFIG_DIR))
    refresh = RefreshThread(treeview_update, statusIcon, wTree)
    refresh.start()

global app_hidden
global log
global logFile
global pid
global statusbar
global context_id

app_hidden = True

gtk.gdk.threads_init()

# prepare the log
pid = os.getpid()
logdir = "/tmp/mintUpdate/"

if not os.path.exists(logdir):
    os.system("mkdir -p " + logdir)
    os.system("chmod a+rwx " + logdir)
    
log = tempfile.NamedTemporaryFile(prefix = logdir, delete=False)
logFile = log.name
try:
    os.system("chmod a+rw %s" % log.name)
except Exception, detail:
    print detail

log.writelines("++ Launching mintUpdate \n")
log.flush()

if (not os.path.exists(CONFIG_DIR)):
    os.system("mkdir -p %s" % CONFIG_DIR)    
    log.writelines("++ Creating %s directory\n" % CONFIG_DIR)
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

    accel_group = gtk.AccelGroup()
    wTree.get_widget("window1").add_accel_group(accel_group)

    # Get the window socket (needed for synaptic later on)
    
    if os.getuid() != 0 :
        # If we're not in root mode do that (don't know why it's needed.. very weird)
        socket = gtk.Socket()
        vbox.pack_start(socket, False, False, 0)
        socket.show()
        window_id = repr(socket.get_id())

    # the treeview
    cr = gtk.CellRendererToggle()
    cr.connect("toggled", toggled, treeview_update, statusbar, context_id)
    column1 = gtk.TreeViewColumn(_("Upgrade"), cr)
    column1.set_cell_data_func(cr, celldatafunction_checkbox)
    column1.set_sort_column_id(UPDATE_CHECKED)
    column1.set_resizable(True)

    column2 = gtk.TreeViewColumn(_("Package"), gtk.CellRendererText(), markup=UPDATE_NAME)
    column2.set_sort_column_id(UPDATE_NAME)
    column2.set_resizable(True)

    column3 = gtk.TreeViewColumn(_("Level"), gtk.CellRendererPixbuf(), pixbuf=UPDATE_LEVEL_PIX)
    column3.set_sort_column_id(UPDATE_LEVEL_STR)
    column3.set_resizable(True)

    column4 = gtk.TreeViewColumn(_("Old version"), gtk.CellRendererText(), text=UPDATE_OLD_VERSION)
    column4.set_sort_column_id(UPDATE_OLD_VERSION)
    column4.set_resizable(True)

    column5 = gtk.TreeViewColumn(_("New version"), gtk.CellRendererText(), text=UPDATE_NEW_VERSION)
    column5.set_sort_column_id(UPDATE_NEW_VERSION)
    column5.set_resizable(True)

    column6 = gtk.TreeViewColumn(_("Size"), gtk.CellRendererText(), text=UPDATE_SIZE_STR)
    column6.set_sort_column_id(UPDATE_SIZE)
    column6.set_resizable(True)

    column7 = gtk.TreeViewColumn(_("Type"), gtk.CellRendererPixbuf(), pixbuf=UPDATE_TYPE_PIX)
    column7.set_sort_column_id(UPDATE_TYPE)
    column7.set_resizable(True)

    treeview_update.set_tooltip_column(UPDATE_TOOLTIP)

    treeview_update.append_column(column7)
    treeview_update.append_column(column3)    
    treeview_update.append_column(column1)
    treeview_update.append_column(column2)
    treeview_update.append_column(column4)
    treeview_update.append_column(column5)    
    treeview_update.append_column(column6)
   
    treeview_update.set_headers_clickable(True)
    treeview_update.set_reorderable(False)
    treeview_update.show()
    
    treeview_update.connect( "button-release-event", menuPopup, treeview_update, statusIcon, wTree )

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
    menuItem4 = gtk.ImageMenuItem(gtk.STOCK_PREFERENCES)
    menuItem4.connect('activate', open_preferences, treeview_update, statusIcon, wTree)
    menu.append(menuItem4)
    menuItem = gtk.ImageMenuItem(gtk.STOCK_QUIT)
    menuItem.connect('activate', quit_cb, wTree.get_widget("window1"), wTree.get_widget("vpaned1"), statusIcon)
    menu.append(menuItem)

    statusIcon.connect('activate', activate_icon_cb, None, wTree)
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
    if os.path.exists("/usr/bin/software-sources") or os.path.exists("/usr/bin/software-properties-gtk") or os.path.exists("/usr/bin/software-properties-kde"):
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
    kernelMenuItem = gtk.ImageMenuItem(gtk.STOCK_EXECUTE)
    kernelMenuItem.get_child().set_text(_("Linux kernels"))
    kernelMenuItem.connect("activate", open_kernels)
    infoMenuItem = gtk.ImageMenuItem(gtk.STOCK_DIALOG_INFO)
    infoMenuItem.get_child().set_text(_("Information"))
    infoMenuItem.connect("activate", open_information)
    visibleColumnsMenuItem = gtk.MenuItem(gtk.STOCK_DIALOG_INFO)
    visibleColumnsMenuItem.get_child().set_text(_("Visible columns"))
    visibleColumnsMenu = gtk.Menu()
    visibleColumnsMenuItem.set_submenu(visibleColumnsMenu)

    typeColumnMenuItem = gtk.CheckMenuItem(_("Type"))
    typeColumnMenuItem.set_active(prefs["type_column_visible"])
    column7.set_visible(prefs["type_column_visible"])
    typeColumnMenuItem.connect("toggled", setVisibleColumn, column7, "type")
    visibleColumnsMenu.append(typeColumnMenuItem)

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

    descriptionsMenuItem = gtk.CheckMenuItem(_("Show descriptions"))
    descriptionsMenuItem.set_active(prefs["descriptions_visible"])    
    descriptionsMenuItem.connect("toggled", setVisibleDescriptions, treeview_update, statusIcon, wTree, prefs)
    viewSubmenu.append(descriptionsMenuItem)

    viewSubmenu.append(historyMenuItem)
    viewSubmenu.append(kernelMenuItem)
    viewSubmenu.append(infoMenuItem)

    helpMenu = gtk.MenuItem(_("_Help"))
    helpSubmenu = gtk.Menu()
    helpMenu.set_submenu(helpSubmenu)
    helpMenuItem = gtk.ImageMenuItem(gtk.STOCK_HELP)
    helpMenuItem.get_child().set_text(_("Contents"))
    helpMenuItem.connect("activate", open_help)
    key, mod = gtk.accelerator_parse("F1")
    helpMenuItem.add_accelerator("activate", accel_group, key, mod, gtk.ACCEL_VISIBLE)
    aboutMenuItem = gtk.ImageMenuItem(gtk.STOCK_ABOUT)
    aboutMenuItem.get_child().set_text(_("About"))
    aboutMenuItem.connect("activate", open_about)
    helpSubmenu.append(helpMenuItem)
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
