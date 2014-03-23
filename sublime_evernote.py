#coding:utf-8
import sys
import os
import json

if sys.version_info < (3, 3):
    raise RuntimeError('The Evernote plugin works with Sublime Text 3 only')

# NOTE: OAuth was not implemented, because the Python 3 that is built into Sublime Text 3 was
# built without SSL. So, among other things, this means no http.client.HTTPSRemoteConnection

package_file = os.path.normpath(os.path.abspath(__file__))
package_path = os.path.dirname(package_file)
lib_path = os.path.join(package_path, "lib")

if lib_path not in sys.path:
    sys.path.append(lib_path)

import evernote.edam.type.ttypes as Types
import evernote.edam.error.ttypes as Errors

# import evernote.edam.userstore.UserStore as UserStore
import evernote.edam.notestore.NoteStore as NoteStore
import thrift.protocol.TBinaryProtocol as TBinaryProtocol
import thrift.transport.THttpClient as THttpClient

import sublime
import sublime_plugin
import webbrowser
import markdown2
import html2text

from base64 import b64encode, b64decode

USER_AGENT = {'User-Agent': 'SublimeEvernote/2.0'}

EVERNOTE_SETTINGS = "Evernote.sublime-settings"
SUBLIME_EVERNOTE_COMMENT_BEG = "<!-- Sublime:"
SUBLIME_EVERNOTE_COMMENT_END = "-->"

DEBUG = True

if DEBUG:
    def LOG(*args):
        print("Evernote:", *args)
else:
    def LOG(*args):
        pass


def extractTags(tags):
    try:
        tags = json.loads(tags)
    except:
        tags = [t.strip(' \t') for t in tags and tags.split(",") or []]
    return tags


# TODO: move to EvernoteDo and remove notebooks arg
def populate_note(note, view, notebooks=[]):
    if view:
        contents = view.substr(sublime.Region(0, view.size()))
        body = markdown2.markdown(contents, extras=EvernoteDo.MD_EXTRAS)
        meta = body.metadata or {}
        content = '<?xml version="1.0" encoding="UTF-8"?>'
        content += '<!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd">'
        content += '<en-note>'
        hidden = ('\n%s%s%s\n' %
                    (SUBLIME_EVERNOTE_COMMENT_BEG,
                     b64encode(contents.encode('utf8')).decode('utf8'),
                     SUBLIME_EVERNOTE_COMMENT_END))
        content += hidden
        content += body
        LOG(body)
        content += '</en-note>'
        note.title = meta.get("title", note.title)
        tags = meta.get("tags", note.tagNames)
        if tags is not None:
            tags = extractTags(tags)
        LOG(tags)
        note.tagNames = tags
        note.content = content
        if "notebook" in meta:
            for nb in notebooks:
                if nb.name == meta["notebook"]:
                    note.notebookGuid = nb.guid
                    break
    return note


def append_to_view(view, text):
    view.run_command('append', {
        'characters': text,
    })
    return view


def find_syntax(lang, default=None):
    res = sublime.find_resources("%s.*Language" % lang)
    if res:
        return res[-1]
    else:
        return (default or ("Packages/%s/%s.tmLanguage" % lang))

def clear_cache():
    EvernoteDo._noteStore = None
    EvernoteDo._notebook_by_name = None
    EvernoteDo._notebook_by_guid = None
    EvernoteDo._notebooks_cache = None
    EvernoteDo._tag_guid_cache = {}
    EvernoteDo._tag_name_cache = {}


class EvernoteDo():

    _noteStore = None

    _notebook_by_guid = None
    _notebook_by_name = None
    _notebooks_cache = None

    _tag_name_cache = {}
    _tag_guid_cache = {}

    MD_EXTRAS = {
        'footnotes'          : None,
        'cuddled-lists'      : None,
        'metadata'           : None,
        'markdown-in-html'   : None,
        'fenced-code-blocks' : {'noclasses': True, 'cssclass': "", 'style': "default"}
    }

    def token(self):
        return self.settings.get("token")

    def load_settings(self):
        self.settings = sublime.load_settings(EVERNOTE_SETTINGS)
        pygm_style = self.settings.get('code_highlighting_style')
        if pygm_style:
            EvernoteDo.MD_EXTRAS['fenced-code-blocks']['style'] = pygm_style
        if self.settings.get("code_friendly"):
            EvernoteDo.MD_EXTRAS['code-friendly'] = None
        css = self.settings.get("inline_css")
        if css is not None:
            EvernoteDo.MD_EXTRAS['inline-css'] = css
        self.md_syntax = self.settings.get("md_syntax")
        if not self.md_syntax:
            self.md_syntax = find_syntax("Markdown")

    def message(self, msg):
        sublime.status_message(msg)

    def connect(self, callback, **kwargs):
        self.message("initializing..., please wait...")

        def __connect(token, noteStoreUrl):
            self.settings.set("token", token)
            self.settings.set("noteStoreUrl", noteStoreUrl)
            sublime.save_settings(EVERNOTE_SETTINGS)
            callback(**kwargs)

        def __derive_note_store_url(token):
            token_parts = token.split(":")
            id = token_parts[0][2:]
            url = "http://www.evernote.com/shard/" + id + "/notestore"
            return url

        def on_token(token):
            noteStoreUrl = self.settings.get("noteStoreUrl")
            if not noteStoreUrl:
                noteStoreUrl = __derive_note_store_url(token)
            __connect(token, noteStoreUrl)

        token = self.token()
        if token:
            noteStoreUrl = self.settings.get("noteStoreUrl")
            if not noteStoreUrl:
                noteStoreUrl = __derive_note_store_url(token)
                __connect(token, noteStoreUrl)
        else:
            webbrowser.open_new_tab("https://www.evernote.com/api/DeveloperToken.action")
            self.window.show_input_panel("Developer Token (required):", "", on_token, None, None)

    def get_note_store(self):
        if EvernoteDo._noteStore:
            return EvernoteDo._noteStore
        noteStoreUrl = self.settings.get("noteStoreUrl")
        noteStoreHttpClient = THttpClient.THttpClient(noteStoreUrl)
        noteStoreHttpClient.setCustomHeaders(USER_AGENT)
        noteStoreProtocol = TBinaryProtocol.TBinaryProtocol(noteStoreHttpClient)
        noteStore = NoteStore.Client(noteStoreProtocol)
        EvernoteDo._noteStore = noteStore
        return noteStore

    def get_notebooks(self):
        if EvernoteDo._notebooks_cache:
            LOG("Using cached notebooks list")
            return EvernoteDo._notebooks_cache
        notebooks = None
        try:
            noteStore = self.get_note_store()
            self.message("Fetching notebooks, please wait...")
            notebooks = noteStore.listNotebooks(self.token())
            self.message("Fetched all notebooks!")
        except Exception as e:
            sublime.error_message('Error getting notebooks: %s' % e)
        EvernoteDo._notebook_by_name = dict([(nb.name, nb) for nb in notebooks])
        EvernoteDo._notebook_by_guid = dict([(nb.guid, nb) for nb in notebooks])
        EvernoteDo._notebooks_cache = notebooks
        return notebooks

    def notebook_from_guid(self, guid):
        self.get_notebooks()  # To trigger caching
        return EvernoteDo._notebook_by_guid[guid]

    def notebook_from_name(self, name):
        self.get_notebooks()  # To trigger caching
        return EvernoteDo._notebook_by_name[name]

    def tag_from_guid(self, guid):
        if guid not in EvernoteDo._tag_name_cache:
            name = self.get_note_store().getTag(self.token(), guid).name
            EvernoteDo._tag_name_cache[guid] = name
            EvernoteDo._tag_guid_cache[name] = guid
        return EvernoteDo._tag_name_cache[guid]

    def tag_from_name(self, name):
        if name not in EvernoteDo._tag_guid_cache:
            # This requires downloading the full list
            tags = self.get_note_store().listTags(self.token())
            for tag in tags:
                EvernoteDo._tag_name_cache[tag.guid] = tag.name
                EvernoteDo._tag_guid_cache[tag.name] = tag.guid
        return EvernoteDo._tag_guid_cache[name]


class EvernoteDoText(EvernoteDo, sublime_plugin.TextCommand):

    def message(self, msg, timeout=5000):
        self.view.set_status("Evernote", msg)
        if timeout:
            sublime.set_timeout(lambda: self.view.erase_status("Evernote"), timeout)

    def run(self, edit, **kwargs):
        if DEBUG:
            from imp import reload
            reload(markdown2)
            reload(html2text)

        self.window = self.view.window()

        self.load_settings()

        if not self.token():
            self.connect(lambda **kw: self.do_run(edit, **kw), **kwargs)
        else:
            self.do_run(edit, **kwargs)


class EvernoteDoWindow(EvernoteDo, sublime_plugin.WindowCommand):

    def run(self, **kwargs):
        if DEBUG:
            from imp import reload
            reload(markdown2)
            reload(html2text)

        self.load_settings()

        if not self.token():
            self.connect(self.do_run, **kwargs)
        else:
            self.do_run(**kwargs)


class SendToEvernoteCommand(EvernoteDoText):

    def do_run(self, edit, **kwargs):
        self.do_send(**kwargs)

    def do_send(self, **args):
        noteStore = self.get_note_store()
        note = Types.Note()

        default_tags = args.get("default_tags", "")

        if "title" in args:
            note.title = args["title"]
        if "notebook" in args:
            try:
                note.notebookGuid = self.notebook_from_name(args["notebook"]).guid
            except:
                note.notebookGuid = None
        if "tags" in args:
            note.tagNames = extractTags(args["tags"])

        notebooks = self.get_notebooks()
        populate_note(note, self.view, notebooks)

        def on_cancel():
            self.message("Note not sent.")

        def choose_title():
            if not note.title:
                self.window.show_input_panel("Title (required):", "", choose_tags, None, on_cancel)
            else:
                choose_tags()

        def choose_tags(title=None):
            if title is not None:
                note.title = title
            if note.tagNames is None:
                self.window.show_input_panel("Tags (Optional):", default_tags, choose_notebook, None, on_cancel)
            else:
                choose_notebook()

        def choose_notebook(tags=None):
            if tags is not None:
                note.tagNames = extractTags(tags)
            if note.notebookGuid is None:
                self.window.show_quick_panel([notebook.name for notebook in notebooks], on_notebook)
            else:
                __send_note(note.notebookGuid)

        def on_notebook(notebook):
            if notebook >= 0:
                __send_note(notebooks[notebook].guid)
            else:
                on_cancel()

        def __send_note(notebookGuid):
            note.notebookGuid = notebookGuid

            LOG(note.title)
            LOG(note.tagNames)
            LOG(note.notebookGuid)
            LOG(note.content)

            try:
                self.message("Posting note, please wait...")
                cnote = noteStore.createNote(self.token(), note)
                self.view.settings().set("$evernote", True)
                self.view.settings().set("$evernote_guid", cnote.guid)
                self.view.settings().set("$evernote_title", cnote.title)
                self.view.set_syntax_file(self.md_syntax)
                if self.view.file_name() is None:
                    self.view.set_name(cnote.title)
                self.message("Successfully posted note: guid:%s" % cnote.guid, 10000)
            except Errors.EDAMUserException as e:
                args = dict(title=note.title, notebookGuid=note.notebookGuid, tags=note.tagNames)
                if e.errorCode == 9:
                    self.connect(self.do_send, **args)
                else:
                    if sublime.ok_cancel_dialog('Evernote complained:\n\n%s\n\nRetry?' % e.parameter):
                        self.connect(self.do_send, **args)
            except Exception as e:
                sublime.error_message('Error %s' % e)

        choose_title()


class SaveEvernoteNoteCommand(EvernoteDoText):

    def do_run(self, edit):
        note = Types.Note()
        noteStore = self.get_note_store()

        note.title = self.view.settings().get("$evernote_title")
        note.guid = self.view.settings().get("$evernote_guid")

        populate_note(note, self.view, self.get_notebooks())

        self.message("Updating note, please wait...")

        def __update_note():
            try:
                cnote = noteStore.updateNote(self.token(), note)
                self.view.settings().set("$evernote", True)
                self.view.settings().set("$evernote_guid", cnote.guid)
                self.view.settings().set("$evernote_title", cnote.title)
                self.message("Successfully updated note: guid:%s" % cnote.guid)
            except Errors.EDAMUserException as e:
                if e.errorCode == 9:
                    self.connect(self.__update_note)
                else:
                    if sublime.ok_cancel_dialog('Evernote complained:\n\n%s\n\nRetry?' % e.parameter):
                        self.connect(self.__update_note)
            except Exception as e:
                sublime.error_message('Error %s' % e)

        __update_note()

    def is_enabled(self):
        if self.view.settings().get("$evernote_guid", False):
            return True
        return False


class OpenEvernoteNoteCommand(EvernoteDoWindow):

    def do_run(self, note_guid=None, by_searching=None,
               from_notebook=None, with_tags=None,
               order=None, ascending=None, **kwargs):
        noteStore = self.get_note_store()
        notebooks = self.get_notebooks()

        search_args = {}

        order = order or self.settings.get("notes_order", "default").upper()
        search_args['order'] = Types.NoteSortOrder._NAMES_TO_VALUES.get(order)  # None = default
        search_args['ascending'] = ascending or self.settings.get("notes_order_ascending", False)

        if from_notebook:
            try:
                search_args['notebookGuid'] = self.notebook_from_name(from_notebook).guid
            except:
                sublime.error_message("Notebook %s not found!" % from_notebook)
                return

        if with_tags:
            if isinstance(with_tags, str):
                with_tags = [with_tags]
            try:
                search_args['tagGuids'] = [self.tag_from_name(name) for name in with_tags]
            except KeyError as e:
                sublime.error_message("Tag %s not found!" % e)

        def notes_panel(notes, show_notebook=False):
            if not notes:
                self.message("No notes found!")  # Should it be a dialog?
                return

            def on_note(i):
                if i < 0:
                    return
                self.message('Retrieving note "%s"...' % notes[i].title)
                self.open_note(notes[i].guid, **kwargs)
            if show_notebook:
                menu = [self.notebook_from_guid(note.notebookGuid).name + ": " + note.title for note in notes]
                # menu = [[note.title, self.notebook_from_guid(note.notebookGuid).name] for note in notes]
            else:
                menu = [note.title for note in notes]
            self.window.show_quick_panel(menu, on_note)

        def on_notebook(notebook):
            if notebook < 0:
                return
            search_args['notebookGuid'] = notebooks[notebook].guid
            notes = self.find_notes(search_args)
            sublime.set_timeout(lambda: notes_panel(notes), 0)

        def do_search(query):
            self.message("Searching notes...")
            search_args['words'] = query
            notes_panel(self.find_notes(search_args), True)

        if note_guid:
            self.open_note(note_guid, **kwargs)
            return

        if by_searching:
            if isinstance(by_searching, str):
                do_search(by_searching)
            else:
                self.window.show_input_panel("Enter search query:", "", do_search, None, None)
            return

        if from_notebook or with_tags:
            notes_panel(self.find_notes(search_args), not from_notebook)
        else:
            self.window.show_quick_panel([notebook.name for notebook in notebooks], on_notebook)

    def find_notes(self, search_args):
        return self.get_note_store().findNotesMetadata(
            self.token(),
            NoteStore.NoteFilter(**search_args),
            None, self.settings.get("max_notes", 100),
            NoteStore.NotesMetadataResultSpec(includeTitle=True, includeNotebookGuid=True)).notes

    def open_note(self, guid, convert=True, **unk_args):
        try:
            noteStore = self.get_note_store()
            note = noteStore.getNote(self.token(), guid, True, False, False, False)
            nb_name = self.notebook_from_guid(note.notebookGuid).name
            newview = self.window.new_file()
            newview.set_scratch(True)
            newview.set_name(note.title)
            LOG(note.content)
            LOG(note.guid)
            if convert:
                # tags = [noteStore.getTag(self.token(), guid).name for guid in (note.tagGuids or [])]
                # tags = [self.tag_from_guid(guid) for guid in (note.tagGuids or [])]
                tags = noteStore.getNoteTagNames(self.token(), note.guid)
                meta = "---\n"
                meta += "title: %s\n" % (note.title or "Untitled")
                meta += "tags: %s\n" % (json.dumps(tags, ensure_ascii=False))
                meta += "notebook: %s\n" % nb_name
                meta += "---\n\n"
                builtin = note.content.find(SUBLIME_EVERNOTE_COMMENT_BEG, 0, 150)
                if builtin >= 0:
                    try:
                        builtin_end = note.content.find(SUBLIME_EVERNOTE_COMMENT_END, builtin)
                        bmdtxt = note.content[builtin+len(SUBLIME_EVERNOTE_COMMENT_BEG):builtin_end]
                        mdtxt = b64decode(bmdtxt.encode('utf8')).decode('utf8')
                        meta = ""
                        LOG("Loaded from built-in comment")
                    except Exception as e:
                        mdtxt = ""
                        LOG("Loading from built-in comment failed", e)
                if builtin < 0 or mdtxt == "":
                    try:
                        mdtxt = html2text.html2text(note.content)
                        LOG("Conversion ok")
                    except Exception as e:
                        mdtxt = note.content
                        LOG("Conversion failed", e)
                newview.settings().set("$evernote", True)
                newview.settings().set("$evernote_guid", note.guid)
                newview.settings().set("$evernote_title", note.title)
                append_to_view(newview, meta+mdtxt)
                syntax = self.md_syntax
            else:
                syntax = find_syntax("XML")
                append_to_view(newview, note.content)
            newview.set_syntax_file(syntax)
            newview.show(0)
            self.message('Note "%s" opened!' % note.title)
        except Errors.EDAMNotFoundException as e:
            sublime.error_message("The note with the specified guid could not be found.")
        except Errors.EDAMUserException:
            sublime.error_message("The specified note could not be found.\nPlease check the guid is correct.")


class AttachToEvernoteNote(OpenEvernoteNoteCommand):

    def open_note(self, guid, insert_in_content=True, **unk_args):
        import hashlib, mimetypes
        view = self.window.active_view()
        filename = view.file_name() or ""
        if view is None:
            self.message("You need to open the file to be attached first!")
            return
        contents = view.substr(sublime.Region(0, view.size())).encode('utf8')
        try:
            noteStore = self.get_note_store()
            note = noteStore.getNote(self.token(), guid, True, False, False, False)
            mime = mimetypes.guess_type(filename)
            h = hashlib.md5(contents)
            if not isinstance(mime, str):
                mime = "text/plain"
            attachment = Types.Resource(
                # noteGuid=guid,
                mime=mime,
                data=Types.Data(body=contents, size=len(contents), bodyHash=h.digest()),
                attributes=Types.ResourceAttributes(
                    fileName=os.path.basename(filename),
                    attachment=True))
            resources = note.resources or []
            resources.append(attachment)
            if insert_in_content and note.content.endswith("</en-note>"):  # just a precaution
                builtin = note.content.find(SUBLIME_EVERNOTE_COMMENT_BEG, 0, 150)
                if builtin >= 0:
                    builtin_end = note.content.find(SUBLIME_EVERNOTE_COMMENT_END, builtin)
                    content = note.content[0:builtin]+note.content[builtin_end+len(SUBLIME_EVERNOTE_COMMENT_END)+1:]
                else:
                    content = note.content
                note.content = content[0:-10] + \
                    '<en-media hash="%s" type="%s"/></en-note>' % (h.hexdigest(), mime)
            note.resources = resources
            noteStore.updateNote(self.token(), note)
            self.message("Succesfully attached to note '%s'" % note.title)
        except Errors.EDAMNotFoundException as e:
            sublime.error_message("The note with the specified guid could not be found.")
        except Errors.EDAMUserException:
            sublime.error_message("The specified note could not be found.\nPlease check the guid is correct.")


class ViewInEvernoteWebappCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        url = self.view.settings().get("noteStoreUrl")[0:-9] + "view/%s"
        self.view.window().run_command("open_url", {"url": url % self.view.settings().get("$evernote_guid")})

    def is_enabled(self):
        if self.view.settings().get("$evernote_guid", False):
            return True
        return False


class NewEvernoteNoteCommand(EvernoteDo, sublime_plugin.WindowCommand):

    def run(self):
        self.load_settings()
        view = self.window.new_file()
        view.set_syntax_file(self.md_syntax)
        view.show(0)
        view.run_command("insert_snippet", {"name": "Packages/Evernote/EvernoteMetadata.sublime-snippet"})


class ReconfigEvernoteCommand(EvernoteDoWindow):

    def run(self):
        self.window = sublime.active_window()
        self.settings = sublime.load_settings(EVERNOTE_SETTINGS)
        self.settings.erase("token")
        clear_cache()
        self.connect(lambda: True)


class ClearEvernoteCacheCommand(sublime_plugin.WindowCommand):

    def run(self):
        clear_cache()
        LOG("Cache cleared!")


class EvernoteListener(sublime_plugin.EventListener):

    def __init__(self):
        self.settings = sublime.load_settings(EVERNOTE_SETTINGS)

    def on_post_save(self, view):
        if self.settings.get('update_on_save'):
            view.run_command("save_evernote_note")

    def on_query_context(self, view, key, operator, operand, match_all):
        if key != "evernote_note":
            return None

        res = view.settings().get("$evernote", False)
        if (operator == sublime.OP_NOT_EQUAL) ^ (not operand):
            res = not res

        return res
