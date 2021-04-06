#!/usr/bin/python3

from gi.repository import Gdk, Gtk, Pango
import re

TAG_DEFINITIONS = {
    'link': {'underline': Pango.Underline.SINGLE, 'foreground': 'blue'}
}

class Link():
    def __init__(self, name, url, start_pos):
        self.name = name
        self.url = url
        self.start_pos = start_pos

class LinkableTextBuffer(Gtk.TextBuffer):
    def __init__(self, view):
        super(LinkableTextBuffer, self).__init__()

        for name, attributes in TAG_DEFINITIONS.items():
            self.create_tag(name, **attributes)

        self.links = []
        self.recursing = False

        self.view = view
        self.view.connect('motion-notify-event', self.track_motion)
        self.view.connect('button-press-event', self.handle_click)

    def track_motion(self, view, event):
        mouse_iter = self.view.get_iter_at_location(*self.view.window_to_buffer_coords(Gtk.TextWindowType.TEXT, event.x, event.y))[1]
        tag = self.get_tag_table().lookup('link')
        if mouse_iter.has_tag(tag):
            self.view.get_window(Gtk.TextWindowType.TEXT).set_cursor(Gdk.Cursor.new_from_name(Gdk.Display.get_default(), 'pointer'))
            return Gdk.EVENT_STOP

        self.view.get_window(Gtk.TextWindowType.TEXT).set_cursor(Gdk.Cursor.new_from_name(Gdk.Display.get_default(), 'text'))

        return Gdk.EVENT_PROPAGATE

    def handle_click(self, view, event):
        if event.button != 1:
            return Gdk.EVENT_PROPAGATE

        tag = self.get_tag_table().lookup('link')
        mouse_iter = self.view.get_iter_at_location(*self.view.window_to_buffer_coords(Gtk.TextWindowType.TEXT, event.x, event.y))[1]

        if not mouse_iter.has_tag(tag):
            return Gdk.EVENT_PROPAGATE

        start_link = mouse_iter.copy()
        start_link.backward_to_tag_toggle(tag)
        end_link = mouse_iter.copy()
        end_link.forward_to_tag_toggle(tag)

        name = self.get_slice(start_link, end_link, False)
        index = start_link.get_offset()
        for link_entry in self.links:
            if link_entry.name == name and link_entry.start_pos == index:
                Gtk.show_uri(None, link_entry.url, event.time)
                break

        return Gdk.EVENT_STOP

    def do_insert_text(self, location, text, length, data=None):
        self.links = []

        def match_func(match):
            link_entry = Link(match.groups()[0], match.groups()[1], match.start(0))
            self.links.append(link_entry)
            return link_entry.name

        previous_text = text
        new_text = ""
        while True:
            new_text = re.sub(r'(?:LINK:\[)([\s\S]+?)(?:\]\[)([\S]+?)(?:\]\:LINK)', match_func, previous_text, count=1)

            if new_text != previous_text:
                previous_text = new_text
                continue

            break

        Gtk.TextBuffer.do_insert_text(self, location, new_text, len(new_text))

        for link in self.links:
            self.apply_tag_by_name("link",
                                   self.get_iter_at_offset(link.start_pos),
                                   self.get_iter_at_offset(link.start_pos + len(link.name)))



