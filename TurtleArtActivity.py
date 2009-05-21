#Copyright (c) 2007, Playful Invention Company
#Copyright (c) 2008-9, Walter Bender

#Permission is hereby granted, free of charge, to any person obtaining a copy
#of this software and associated documentation files (the "Software"), to deal
#in the Software without restriction, including without limitation the rights
#to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#copies of the Software, and to permit persons to whom the Software is
#furnished to do so, subject to the following conditions:

#The above copyright notice and this permission notice shall be included in
#all copies or substantial portions of the Software.

#THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
#THE SOFTWARE.

import tawindow
import talogo

import pygtk
pygtk.require('2.0')
import gtk
import gobject

import logging
_logger = logging.getLogger('turtleart-activity')

import sugar
from sugar.activity import activity
from sugar.graphics.toolbutton import ToolButton
from sugar.graphics.menuitem import MenuItem
from sugar.datastore import datastore

import telepathy
from dbus.service import method, signal
from dbus.gobject_service import ExportedGObject
from sugar.presence import presenceservice
from sugar.presence.tubeconn import TubeConnection

from sugar import profile
from gettext import gettext as _
import locale
import os.path
import subprocess
import tarfile
import sys
from taexporthtml import *
from taexportlogo import *
import re

SERVICE = 'org.laptop.TurtleArtActivity'
IFACE = SERVICE
PATH = '/org/laptop/TurtleArtActivity'

class TurtleArtActivity(activity.Activity):

    def __init__(self, handle):
        super(TurtleArtActivity,self).__init__(handle)

        try:
            datapath = os.path.join(activity.get_activity_root(), "data")
        except:
            # Early versions of Sugar (e.g., 656) didn't support
            # get_activity_root()
            datapath = os.path.join( \
                os.environ['HOME'], \
                ".sugar/default/org.laptop.TurtleArtActivity/data")

        self.toolbox = activity.ActivityToolbox(self)
        self.set_toolbox(self.toolbox)

        # Notify when the visibility state changes
        self.add_events(gtk.gdk.VISIBILITY_NOTIFY_MASK)
        self.connect("visibility-notify-event", self.__visibility_notify_cb)

        # Add additional panels
        self.projectToolbar = ProjectToolbar(self)
        self.toolbox.add_toolbar( _('Project'), self.projectToolbar )
        self.editToolbar = EditToolbar(self)
        self.toolbox.add_toolbar(_('Edit'), self.editToolbar)
        self.saveasToolbar = SaveAsToolbar(self)
        self.toolbox.add_toolbar( _('Save as'), self.saveasToolbar )
        self.toolbox.show()

        # Set the project toolbar as the initial one selected
        self.toolbox.set_current_toolbar(1)

        # Create a scrolled window to contain the turtle canvas
        self.sw = gtk.ScrolledWindow()
 #       self.fixed = gtk...
        self.set_canvas(self.sw)
        self.sw.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
        self.sw.show()
        canvas = gtk.DrawingArea()
        canvas.set_size_request(gtk.gdk.screen_width()*2, \
                                gtk.gdk.screen_height()*2)
        self.sw.add_with_viewport(canvas)
        canvas.show()

        self.toolbox._activity_toolbar.title.grab_focus()
        self.toolbox._activity_toolbar.title.select_region(0,0)

        """
        To be replaced with date checking in tasetup.py; 
        each language group should be stored in it's own sub-directory
        """
        # Check to see if the version or language has changed
        try:
            version = os.environ['SUGAR_BUNDLE_VERSION']
        except:
            version = " unknown"

        lang = locale.getdefaultlocale()[0]
        if not lang:
            lang = 'en'
        lang = lang[0:2]
        if not os.path.isdir(os.path.join(activity.get_bundle_path(), \
                             'images', lang)):
            lang = 'en'

        # If either has changed, remove the old png files
        filename = "version.dat"
        versiondata = []
        newversion = True
        try:
            FILE = open(os.path.join(datapath, filename), "r")
            if FILE.readline() == lang + version:
                newversion = False
            else:
                _logger.debug("out with the old, in with the new")
                cmd = "rm " + os.path.join(datapath, '*.png')
                subprocess.check_call(cmd, shell=True)
        except:
            _logger.debug("writing new version data")
            _logger.debug("and creating a tamyblock.py Journal entry")

        """
        Make sure there is a copy of tamyblock.py in the Journal
        """
        if newversion is True:
            dsobject = datastore.create()
            dsobject.metadata['title'] = 'tamyblock.py'
            dsobject.metadata['icon-color'] = \
                profile.get_color().to_string()
            dsobject.metadata['mime_type'] = 'text/x-python'
            dsobject.set_file_path(os.path.join( \
                activity.get_bundle_path(), 'tamyblock.py'))
            datastore.write(dsobject)
            dsobject.destroy()

        versiondata.append(lang + version)
        FILE = open(os.path.join(datapath, filename), "w")
        FILE.writelines(versiondata)
        FILE.close()

        # Initialize the turtle art canvas
        self.tw = tawindow.twNew(canvas,activity.get_bundle_path(), \
                                 lang, self)
        self.tw.activity = self
        self.tw.window.grab_focus()
        self.tw.save_folder=os.path.join( \
            os.environ['SUGAR_ACTIVITY_ROOT'], 'data')

        if self._jobject and self._jobject.file_path:
            self.read_file(self._jobject.file_path)

        """
        A simplistic sharing model: the sharer is the master;
        TODO: hand off role of master is sharer leaves
        """
        # Get the Presence Service
        self.pservice = presenceservice.get_instance()
        self.initiating = None # sharing (True) or joining (False)

        # Add my buddy object to the list
        owner = self.pservice.get_owner()
        self.owner = owner
        self.tw.buddies.append(self.owner)
        self._share = ""

        self.connect('shared', self._shared_cb)
        self.connect('joined', self._joined_cb)

    """
    Recenter scrolled window around canvas
    """
    def recenter(self):
        hadj = self.sw.get_hadjustment()
        # print hadj
        hadj.set_value(0)
        self.sw.set_hadjustment(hadj)
        vadj = self.sw.get_vadjustment()
        # print vadj
        vadj.set_value(0)
        self.sw.set_vadjustment(vadj)

    """
    Either set up initial share...
    """
    def _shared_cb(self, activity):
        if self._shared_activity is None:
            _logger.error("Failed to share or join activity ... \
                _shared_activity is null in _shared_cb()")
            return

        self.initiating = True
        self.waiting_for_blocks = False
        _logger.debug('I am sharing...')

        self.conn = self._shared_activity.telepathy_conn
        self.tubes_chan = self._shared_activity.telepathy_tubes_chan
        self.text_chan = self._shared_activity.telepathy_text_chan
        
        # call back for "NewTube" signal
        self.tubes_chan[telepathy.CHANNEL_TYPE_TUBES].connect_to_signal \
            ('NewTube', self._new_tube_cb)

        _logger.debug('This is my activity: making a tube...')
        id = self.tubes_chan[telepathy.CHANNEL_TYPE_TUBES].OfferDBusTube(
            SERVICE, {})

    """
    ...or join an exisiting share.
    """
    def _joined_cb(self, activity):
        if self._shared_activity is None:
            _logger.error("Failed to share or join activity ... \
                _shared_activity is null in _shared_cb()")
            return

        self.initiating = False
        _logger.debug('I joined a shared activity.')

        self.conn = self._shared_activity.telepathy_conn
        self.tubes_chan = self._shared_activity.telepathy_tubes_chan
        self.text_chan = self._shared_activity.telepathy_text_chan
        
        # call back for "NewTube" signal
        self.tubes_chan[telepathy.CHANNEL_TYPE_TUBES].connect_to_signal( \
            'NewTube', self._new_tube_cb)

        _logger.debug('I am joining an activity: waiting for a tube...')
        self.tubes_chan[telepathy.CHANNEL_TYPE_TUBES].ListTubes(
            reply_handler=self._list_tubes_reply_cb, 
            error_handler=self._list_tubes_error_cb)

        # joiner should request current state from sharer
        self.waiting_for_blocks = True

    def _list_tubes_reply_cb(self, tubes):
        for tube_info in tubes:
            self._new_tube_cb(*tube_info)

    def _list_tubes_error_cb(self, e):
        _logger.error('ListTubes() failed: %s', e)

    """
    Create a new tube
    """
    def _new_tube_cb(self, id, initiator, type, service, params, state):
        _logger.debug('New tube: ID=%d initator=%d type=%d service=%s '
                     'params=%r state=%d', id, initiator, type, service, 
                     params, state)

        if (type == telepathy.TUBE_TYPE_DBUS and service == SERVICE):
            if state == telepathy.TUBE_STATE_LOCAL_PENDING:
                self.tubes_chan[ \
                              telepathy.CHANNEL_TYPE_TUBES].AcceptDBusTube(id)

            tube_conn = TubeConnection(self.conn, 
                self.tubes_chan[telepathy.CHANNEL_TYPE_TUBES], id, \
                group_iface=self.text_chan[telepathy.CHANNEL_INTERFACE_GROUP])

            # we'll use a chat tube to send serialized stacks back and forth
            self.chattube = ChatTube(tube_conn, self.initiating, \
                self.event_received_cb)

            # now that we have the tube, we can ask for an initialization
            if self.waiting_for_blocks is True:
                self._send_event("i")

    """
    Handle the receiving of events in share
    Events are sent as a tuple
        cmd:data
    where cmd is a mouse or keyboard event and data are x,y coordinates
    or a keysroke
    """
    def event_received_cb(self, text):
        # maybe we can use a stack to share events to new-comers?
        # self._share += "text + "\n"
        if text[0] == 'p': # button press
            e,x,y,mask = re.split(":",text)
            # _logger.debug("receiving button press: "+x+" "+y+" "+mask)
            if mask == 'T':
                tawindow.button_press(self.tw,True,int(x),int(y),False)
            else:
                tawindow.button_press(self.tw,False,int(x),int(y),False)
        elif text[0] == 'r': # block release
            e,x,y = re.split(":",text)
            # _logger.debug("receiving button release: " + x + " " + y)
            tawindow.button_release(self.tw,int(x),int(y),False)
        elif text[0] == 'm': # mouse move
            e,x,y = re.split(":",text)
            _logger.debug("receiving move: " + x + " " + y)
            tawindow.mouse_move(self.tw,0,0,False,int(x),int(y))
        elif text[0] == 'k': # typing
            e,mask,keyname = re.split(":",text,3)
            # _logger.debug("recieving key press: " + mask + " " + keyname)
            if mask == 'T':
                tawindow.key_press(self.tw,True,keyname,False)
            else:
                tawindow.key_press(self.tw,False,keyname,False)
        elif text[0] == 'i': # request for current state
            # sharer should send current state to joiner
            if self.initiating is True:
                _logger.debug("serialize the project and send to joiner")
                text = tawindow.save_string(self.tw)
                self._send_event("I:" + text)
                tawindow.show_palette(self.tw)
        elif text[0] == 'I': # receiving current state
            if self.waiting_for_blocks:
                _logger.debug("receiving project from sharer")
                e,text = re.split(":",text,2)
                # unpack data
                tawindow.load_string(self.tw,text)
                # all caught up
                self.waiting_for_blocks = False

    """
    Send event through the tube
    """
    def _send_event(self, entry):
        # nick = profile.get_nick_name()
        # nick = nick.upper()
        if hasattr(self, 'chattube') and self.chattube is not None:
            self.chattube.SendText(entry)

    """
    Callback method for when the activity's visibility changes
    """
    def __visibility_notify_cb(self, window, event):
        if event.state == gtk.gdk.VISIBILITY_FULLY_OBSCURED:
#            _logger.debug("I am not visible so I should free the audio")
            self.tw.lc.ag = None
        elif event.state in \
            [gtk.gdk.VISIBILITY_UNOBSCURED, gtk.gdk.VISIBILITY_PARTIAL]:
            pass

    def update_title_cb(self, widget, event, toolbox):
        toolbox._activity_toolbar._update_title_cb()
        toolbox._activity_toolbar._update_title_sid = True

    def _keep_clicked_cb(self, button):
        self.jobject_new_patch()

    """
    Write the project and a screen snapshot to the Journal
    """
    def write_file(self, file_path):
        _logger.debug("Writing file %s" % file_path)
        self.metadata['mime_type'] = 'application/x-tar'
        import tempfile
        tar_fd = tarfile.open(file_path, 'w')
        pngfd, pngfile = tempfile.mkstemp(".png")
        tafd, tafile = tempfile.mkstemp(".ta")
        del pngfd
        del tafd

        try:
            tawindow.save_data(self.tw,tafile)
            tawindow.save_pict(self.tw,pngfile)
            tar_fd.add(tafile, "ta_code.ta")
            tar_fd.add(pngfile, "ta_image.png") 

        finally:
            tar_fd.close()
            os.remove(pngfile)
            os.remove(tafile)

    """
    Read a project in and then run it
    """
    def read_file(self, file_path):
        import tarfile,os,tempfile,shutil

        if hasattr(self, 'tw'):
            _logger.debug("Reading file %s" %  file_path)
            # should be a tar file
            if file_path[-5:] == ".gtar":
                tar_fd = tarfile.open(file_path, 'r')
                tmpdir = tempfile.mkdtemp()
                try:
                    # We'll get 'ta_code.ta' and possibly a 'ta_image.png'
                    tar_fd.extractall(tmpdir)
                    tawindow.load_files(self.tw, os.path.join(tmpdir, \
                        'ta_code.ta'), os.path.join(tmpdir, 'ta_image.png'))
                finally:
                    shutil.rmtree(tmpdir)
                    tar_fd.close()
            # try to open a .ta file
            elif file_path[-3:] == ".ta":
                tawindow.load_files(self.tw, file_path, "")
            # run the activity
            tawindow.runbutton(self.tw, 0)
        else:
            _logger.debug("Deferring reading file %s" %  file_path)

    """
    Save instance to Journal
    """
    def jobject_new_patch(self):
        oldj = self._jobject
        self._jobject = datastore.create()
        self._jobject.metadata['title'] = oldj.metadata['title']
        self._jobject.metadata['title_set_by_user'] = \
            oldj.metadata['title_set_by_user']
        self._jobject.metadata['activity'] = self.get_service_name()
        self._jobject.metadata['activity_id'] = self.get_id()
        self._jobject.metadata['keep'] = '0'
        # Is this the correct syntax for saving the buddies list?
        self._jobject.metadata['buddies'] = self.tw.buddies
        self._jobject.metadata['preview'] = ''
        self._jobject.metadata['icon-color'] = profile.get_color().to_string()
        self._jobject.file_path = ''
        datastore.write(self._jobject,
                reply_handler=self._internal_jobject_create_cb,
                error_handler=self._internal_jobject_error_cb)
        self._jobject.destroy()

"""
Class for setting up tube for sharing
"""
class ChatTube(ExportedGObject):
 
    def __init__(self, tube, is_initiator, stack_received_cb):
        super(ChatTube, self).__init__(tube, PATH)
        self.tube = tube
        self.is_initiator = is_initiator # Are we sharing or joining activity?
        self.stack_received_cb = stack_received_cb
        self.stack = ''

        self.tube.add_signal_receiver(self.send_stack_cb, 'SendText', IFACE, \
            path=PATH, sender_keyword='sender')

    def send_stack_cb(self, text, sender=None):
        if sender == self.tube.get_unique_name():
            return
        self.stack = text
        self.stack_received_cb(text)

    @signal(dbus_interface=IFACE, signature='s')
    def SendText(self, text):
        self.stack = text

"""
Edit toolbar: copy and paste text and stacks
"""
class EditToolbar(gtk.Toolbar):
    def __init__(self, pc):
        gtk.Toolbar.__init__(self)
        self.activity = pc

        # Copy button
        self.copy = ToolButton( "edit-copy" )
        self.copy.set_tooltip(_('copy'))
        self.copy.props.sensitive = True
        self.copy.connect('clicked', self._copy_cb)
        self.copy.props.accelerator = '<Ctrl>C'
        self.insert(self.copy, -1)
        self.copy.show()

        # Paste button
        self.paste = ToolButton( "edit-paste" )
        self.paste.set_tooltip(_('paste'))
        self.paste.props.sensitive = True
        self.paste.connect('clicked', self._paste_cb)
        self.paste.props.accelerator = '<Ctrl>V'
        self.insert(self.paste, -1)
        self.paste.show()

    def _copy_cb(self, button):
        clipBoard = gtk.Clipboard()
        _logger.debug("serialize the project and copy to clipboard")
        text = tawindow.serialize_stack(self.activity.tw)
        clipBoard.set_text(text)

    def _paste_cb(self, button):
        clipBoard = gtk.Clipboard()
        _logger.debug("paste to the project")
        text = clipBoard.wait_for_text()
        if text is not None:
            tawindow.clone_stack(self.activity.tw,text)

"""
SaveAs toolbar: (1) load samples; (2) save as HTML; (3) save as LOGO;
(4) save as PNG; and (5) import Python code.
"""
class SaveAsToolbar(gtk.Toolbar):
    def __init__(self, pc):
        gtk.Toolbar.__init__(self)
        self.activity = pc

        # project open
        self.sampb = ToolButton( "stock-open" )
        self.sampb.set_tooltip(_('samples'))
        self.sampb.props.sensitive = True
        self.sampb.connect('clicked', self.do_samples)
        self.sampb.props.accelerator = _('<Alt>o')
        self.insert(self.sampb, -1)
        self.sampb.show()

        separator = gtk.SeparatorToolItem()
        separator.set_draw(True)
        self.insert(separator, -1)
        separator.show()

        # HTML save source button
        self.savehtml = ToolButton( "htmloff" )
        self.savehtml.set_tooltip(_('save as HTML'))
        self.savehtml.props.sensitive = True
        self.savehtml.connect('clicked', self.do_savehtml)
        self.insert(self.savehtml, -1)
        self.savehtml.show()

        # Berkeley Logo save source button
        self.savelogo = ToolButton( "logo-saveoff" )
        self.savelogo.set_tooltip(_('save Logo'))
        self.savelogo.props.sensitive = True
        self.savelogo.connect('clicked', self.do_savelogo)
        self.insert(self.savelogo, -1)
        self.savelogo.show()

        # Save as image button
        self.saveimage = ToolButton( "image-saveoff" )
        self.saveimage.set_tooltip(_('save as image'))
        self.saveimage.props.sensitive = True
        self.saveimage.connect('clicked', self.do_saveimage)
        self.insert(self.saveimage, -1)
        self.saveimage.show()

        separator = gtk.SeparatorToolItem()
        separator.set_draw(True)
        self.insert(separator, -1)
        separator.show()

        # Pippy load myblock source button
        self.loadmyblock = ToolButton( "pippy-openoff" )
        self.loadmyblock.set_tooltip(_('load my block'))
        self.loadmyblock.props.sensitive = True
        self.loadmyblock.connect('clicked', self.do_loadmyblock)
        self.insert(self.loadmyblock, -1)
        self.loadmyblock.show()

    def do_samples(self, button):
        tawindow.load_file(self.activity.tw)
        # run the activity
        tawindow.runbutton(self.activity.tw, 0)

    def do_saveimage(self, button):
        self.saveimage.set_icon("image-saveon")
        _logger.debug("saving image to journal")
        import tempfile
        pngfd, pngfile = tempfile.mkstemp(".png")
        del pngfd
        tawindow.save_pict(self.activity.tw,pngfile)

        # Create a datastore object
        file_dsobject = datastore.create()

        # Write metadata
        file_dsobject.metadata['title'] = "Turtle Art image"
        file_dsobject.metadata['icon-color'] = profile.get_color().to_string()
        file_dsobject.metadata['mime_type'] = 'image/png'
        file_dsobject.set_file_path(pngfile)

        datastore.write(file_dsobject)
        file_dsobject.destroy()
        gobject.timeout_add(250,self.saveimage.set_icon, "image-saveoff")
        return

    def do_savehtml(self, button):
        # write html out to datastore
        self.savehtml.set_icon("htmlon")
        _logger.debug("saving html code")
        # til we add the option
        embed_flag = True

        # grab code from stacks
        html = save_html(self,self.activity.tw,embed_flag)
        if len(html) == 0:
            return

        # save the html code to the instance directory
        try:
            datapath = os.path.join(activity.get_activity_root(), "instance")
        except:
            # early versions of Sugar (656) didn't support get_activity_root()
            datapath = os.path.join( \
                os.environ['HOME'], \
                ".sugar/default/org.laptop.TurtleArtActivity/instance")

        html_file = os.path.join(datapath, "portfolio.html")
        f = file(html_file, "w")
        f.write(html)
        f.close()

        if embed_flag == False:
        # need to make a tarball that includes the images
            tar_path = os.path.join(datapath, 'portfolio.tar')
            tar_fd = tarfile.open(tar_path, 'w')
            try:
                tar_fd.add(html_file, "portfolio.html")
                import glob
                image_list = glob.glob(os.path.join(datapath, 'image*'))
                for i in image_list:
                    tar_fd.add(i, os.path.basename(i))
            finally:
                tar_fd.close()

        # Create a datastore object
        file_dsobject = datastore.create()

        # Write any metadata (here we specifically set the title of the file
        # and specify that this is a plain text file). 
        file_dsobject.metadata['title'] = "Turtle Art portfolio"
        file_dsobject.metadata['icon-color'] = profile.get_color().to_string()
        if embed_flag == True:
            file_dsobject.metadata['mime_type'] = 'text/html'
            file_dsobject.set_file_path(html_file)
        else:
            file_dsobject.metadata['mime_type'] = 'application/x-tar'
            file_dsobject.set_file_path(tar_path)

        datastore.write(file_dsobject)
        file_dsobject.destroy()
        gobject.timeout_add(250,self.savehtml.set_icon, "htmloff")
        return

    def do_savelogo(self, button):
        # write logo code out to datastore
        self.savelogo.set_icon("logo-saveon")
        # grab code from stacks
        logocode = save_logo(self,self.activity.tw)
        if len(logocode) == 0:
            return
        filename = "logosession.lg"

        # Create a datastore object
        file_dsobject = datastore.create()

        # Write any metadata (here we specifically set the title of the file
        # and specify that this is a plain text file). 
        file_dsobject.metadata['title'] = filename
        file_dsobject.metadata['mime_type'] = 'text/plain'
        file_dsobject.metadata['icon-color'] = profile.get_color().to_string()

        # save the html code to the instance directory
        try:
            datapath = os.path.join(activity.get_activity_root(), "instance")
        except:
            # Early versions of Sugar (656) didn't support get_activity_root()
            datapath = os.path.join( \
                os.environ['HOME'], \
                ".sugar/default/org.laptop.TurtleArtActivity/instance")

        # Write the file to the data directory of this activity's root. 
        file_path = os.path.join(datapath, filename)
        f = open(file_path, 'w')
        try:
            f.write(logocode)
        finally:
            f.close()

        # Set the file_path in the datastore.
        file_dsobject.set_file_path(file_path)

        datastore.write(file_dsobject)
        gobject.timeout_add(250,self.savelogo.set_icon, "logo-saveoff")
        return

    def do_loadmyblock(self, button):
        self.loadmyblock.set_icon("pippy-openon")
        self.import_py()
        gobject.timeout_add(250,self.loadmyblock.set_icon, "pippy-openoff")
        return

    # Import Python code from the Journal to load into "myblock"
    def import_py(self):
        from sugar.graphics.objectchooser import ObjectChooser
        chooser = ObjectChooser('Python code', None, gtk.DIALOG_MODAL | \
            gtk.DIALOG_DESTROY_WITH_PARENT)
        try:
            result = chooser.run()
            if result == gtk.RESPONSE_ACCEPT:
                dsobject = chooser.get_selected_object()
                try:
                    _logger.debug("opening %s " % dsobject.file_path)
                    FILE = open(dsobject.file_path, "r")
                    self.activity.tw.myblock = FILE.read()
                    FILE.close()
	            tawindow.set_userdefined(self.activity.tw)
                except:
                    _logger.debug("couldn't open %s" % dsobject.file_path)
                dsobject.destroy()
        finally:
            chooser.destroy()
            del chooser

"""
Project toolbar: show/hide palettes; show/hide blocks; run; walk; stop; erase;
                 load sample project; fullscreen
"""
class ProjectToolbar(gtk.Toolbar):

    def __init__(self, pc):
        gtk.Toolbar.__init__(self)
        self.activity = pc

        # palette button (blocks)
        self.palette = ToolButton( "blocksoff" )
        self.palette.set_tooltip(_('hide palette'))
        self.palette.props.sensitive = True
        self.palette.connect('clicked', self.do_palette)
        self.palette.props.accelerator = _('<Alt>p')
        self.insert(self.palette, -1)
        self.palette.show()

        # blocks button (hideshow)
        self.blocks = ToolButton( "hideshowoff" )
        self.blocks.set_tooltip(_('hide blocks'))
        self.blocks.props.sensitive = True
        self.blocks.connect('clicked', self.do_hideshow)
        self.blocks.props.accelerator = _('<Alt>b')
        self.insert(self.blocks, -1)
        self.blocks.show()

        separator = gtk.SeparatorToolItem()
        separator.set_draw(True)
        self.insert(separator, -1)
        separator.show()

        # run button
        self.runproject = ToolButton( "run-fastoff" )
        self.runproject.set_tooltip(_('run'))
        self.runproject.props.sensitive = True
        self.runproject.connect('clicked', self.do_run)
        self.runproject.props.accelerator = _('<Alt>r')
        self.insert(self.runproject, -1)
        self.runproject.show()

        # step button
        self.stepproject = ToolButton( "run-slowoff" )
        self.stepproject.set_tooltip(_('step'))
        self.stepproject.props.sensitive = True
        self.stepproject.connect('clicked', self.do_step)
        self.stepproject.props.accelerator = _('<Alt>w')
        self.insert(self.stepproject, -1)
        self.stepproject.show()

        # stop button
        self.stop = ToolButton( "stopitoff" )
        self.stop.set_tooltip(_('stop turtle'))
        self.stop.props.sensitive = True
        self.stop.connect('clicked', self.do_stop)
        self.stop.props.accelerator = _('<Alt>s')
        self.insert(self.stop, -1)
        self.stop.show()

        separator = gtk.SeparatorToolItem()
        separator.set_draw(True)
        self.insert(separator, -1)
        separator.show()

        # eraser button
        self.eraser = ToolButton( "eraseron" )
        self.eraser.set_tooltip(_('clean'))
        self.eraser.props.sensitive = True
        self.eraser.connect('clicked', self.do_eraser)
        self.eraser.props.accelerator = _('<Alt>e')
        self.insert(self.eraser, -1)
        self.eraser.show()

        separator = gtk.SeparatorToolItem()
        separator.set_draw(True)
        self.insert(separator, -1)
        separator.show()

        # full screen
        self.fullscreenb = ToolButton( "view-fullscreen" )
        self.fullscreenb.set_tooltip(_('fullscreen'))
        self.fullscreenb.props.sensitive = True
        self.fullscreenb.props.accelerator = '<Alt>Enter'
        self.fullscreenb.connect('clicked', self.do_fullscreen)
        self.insert(self.fullscreenb, -1)
        self.fullscreenb.show()

    def do_palette(self, button):
        if self.activity.tw.palette == True:
            tawindow.hideshow_palette(self.activity.tw,False)
            self.palette.set_icon("blockson")
            self.palette.set_tooltip(_('show palette'))
        else:
            tawindow.hideshow_palette(self.activity.tw,True)
            self.palette.set_icon("blocksoff")
            self.palette.set_tooltip(_('hide palette'))

    def do_hidepalette(self):
        # print "in do_hidepalette"
        self.palette.set_icon("blockson")
        self.palette.set_tooltip(_('show palette'))

    def do_showpalette(self):
        # print "in do_showpalette"
        self.palette.set_icon("blocksoff")
        self.palette.set_tooltip(_('hide palette'))
 
    def do_run(self, button):
        self.runproject.set_icon("run-faston")
        self.stop.set_icon("stopiton")
        tawindow.runbutton(self.activity.tw, 0)
        gobject.timeout_add(1000,self.runproject.set_icon,"run-fastoff")
        gobject.timeout_add(1000,self.stepproject.set_icon,"run-slowoff")

    def do_step(self, button):
        self.stepproject.set_icon("run-slowon")
        self.stop.set_icon("stopiton")
        tawindow.runbutton(self.activity.tw, 3)
        gobject.timeout_add(1000,self.stepproject.set_icon,"run-slowoff")
        gobject.timeout_add(1000,self.runproject.set_icon,"run-fastoff")

    def do_stop(self, button):
        self.stop.set_icon("stopitoff")
        tawindow.stop_button(self.activity.tw)
        self.stepproject.set_icon("run-slowoff")
        self.runproject.set_icon("run-fastoff")

    def do_hideshow(self, button):
        tawindow.hideshow_button(self.activity.tw)
        if self.activity.tw.hide == True: # we just hid the blocks
            self.blocks.set_icon("hideshowon")
            self.blocks.set_tooltip(_('show blocks'))
        else:
            self.blocks.set_icon("hideshowoff")
            self.blocks.set_tooltip(_('hide blocks'))
        # update palette buttons too
        if self.activity.tw.palette == False: 
            self.palette.set_icon("blockson")
            self.palette.set_tooltip(_('show palette'))
        else:
            self.palette.set_icon("blocksoff")
            self.palette.set_tooltip(_('hide palette'))

    def do_hide(self):
        self.blocks.set_icon("hideshowon")
        self.blocks.set_tooltip(_('show blocks'))
        self.palette.set_icon("blockson")
        self.palette.set_tooltip(_('show palette'))

    def do_show(self):
        self.blocks.set_icon("hideshowoff")
        self.blocks.set_tooltip(_('hide blocks'))
        self.palette.set_icon("blocksoff")
        self.palette.set_tooltip(_('hide palette'))

    def do_eraser(self, button):
        self.eraser.set_icon("eraseroff")
        self.activity.recenter()
        tawindow.eraser_button(self.activity.tw)
        gobject.timeout_add(250,self.eraser.set_icon,"eraseron")

    def do_fullscreen(self, button):
        self.activity.fullscreen()
        self.activity.recenter()

