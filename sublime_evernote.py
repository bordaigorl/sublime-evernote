#coding:utf-8
import sys
import os
import json
import re

try:
    import ssl
except:
    ssl = None

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
from evernote.edam.error.ttypes import EDAMErrorCode, EDAMUserException, EDAMSystemException, EDAMNotFoundException

# import evernote.edam.userstore.UserStore as UserStore
import evernote.edam.notestore.NoteStore as NoteStore
import thrift.protocol.TBinaryProtocol as TBinaryProtocol
import thrift.transport.THttpClient as THttpClient
from socket import gaierror

import sublime
import sublime_plugin
import webbrowser
import markdown2
import html2text

from datetime import datetime

from base64 import b64encode, b64decode

EVERNOTE_PLUGIN_VERSION = "2.7.0"
USER_AGENT = {'User-Agent': 'SublimeEvernote/' + EVERNOTE_PLUGIN_VERSION}

EVERNOTE_SETTINGS = "Evernote.sublime-settings"
SUBLIME_EVERNOTE_COMMENT_BEG = "<!-- Sublime:"
SUBLIME_EVERNOTE_COMMENT_END = "-->"

DEBUG = False


def LOG(*args):
    if DEBUG:
        print("Evernote:", *args)


def extractTags(tags):
    try:
        tags = json.loads(tags)
    except:
        tags = [t.strip(' \t') for t in tags and tags.split(",") or []]
    return tags


# From markdown2.py
# I know this is ugly but will do until we have a better general solution for metadata
METADATA_PAT = re.compile("""^---[ \t]*\n((?:[ \t]*[^ \t:]+[ \t]*:[^\n]*\n)+)---[ \t]*\n""")

def extract_metadata(text):
    metadata = {}
    tail = text
    if text.startswith("---"):  # fast test
        match = METADATA_PAT.match(text)
        if match:
            tail = text[match.end():]
            metadata_str = match.group(1).strip()
            for line in metadata_str.split('\n'):
                key, value = line.split(':', 1)
                metadata[key.strip()] = value.strip()
            if "tags" in metadata:
                metadata["tags"] = extractTags(metadata["tags"])

    return {"metadata": metadata, "contents": tail.lstrip('\n')}


METADATA_HEADER = """\
---
title: %s
tags: %s
notebook: %s
---

"""


def metadata_header(title="", tags=[], notebook="", **kw):
    return METADATA_HEADER % (title, json.dumps(tags, ensure_ascii=False), notebook)

def set_view_metadata(view, note, reset_modified=True):
    view.settings().set("$evernote", True)
    view.settings().set("$evernote_guid", note.guid)
    view.settings().set("$evernote_title", note.title)
    if reset_modified:
        note_is_current(view)

def note_is_current(view):
    view.settings().set("$evernote_modified", view.change_count())


def insert_to_view(view, text):
    view.run_command('insert', {
        'characters': text,
    })
    return view

def replace_view_text(view, text):
    view.run_command('replace_view_text', {
        'characters': text
    })
    return view

def find_syntax(lang, default=None):
    res = sublime.find_resources("%s.*Language" % lang)
    if res:
        return res[-1]
    else:
        return (default or ("Packages/%s/%s.tmLanguage" % (lang, lang)))


def language_name(scope):
    for s in scope.split(' '):
        names = s.split('.')
        if s.startswith("source."):
            return names[1]
        elif s.startswith("text."):
            if "markdown" in names:  # deal with plugins for MD
                return "markdown"
            elif "latex" in names:  # deal with plugins for LaTeX
                return "latex"
            elif names[1] == "plain":
                return ""
            else:
                return names[-1]
    return ""


def datestr(d):
    d = datetime.fromtimestamp(d // 1000)
    n = datetime.now()
    delta = n - d
    if delta.days == 0:
        if delta.seconds <= 3600 == 0:
            if delta.seconds <= 60 == 0:
                return "just now"
            else:
                return "few minutes ago"
        else:
            return "few hours ago"
    elif delta.days == 1:
        return "yesterday"
    elif delta.days == 2:
        return "2 days ago"
    return d.strftime("on %d/%m/%y")


ecode = EDAMErrorCode
error_groups = {
        'server': ('Internal server error', [ecode.UNKNOWN, ecode.INTERNAL_ERROR, ecode.SHARD_UNAVAILABLE, ecode.UNSUPPORTED_OPERATION ]),
        'data': ('User supplied data is invalid or conflicting', [ecode.BAD_DATA_FORMAT, ecode.DATA_REQUIRED, ecode.DATA_CONFLICT, ecode.LEN_TOO_SHORT, ecode.LEN_TOO_LONG, ecode.TOO_FEW, ecode.TOO_MANY]),
        'permission': ('Action not allowed, permission denied or limits exceeded', [ecode.PERMISSION_DENIED, ecode.LIMIT_REACHED, ecode.QUOTA_REACHED, ecode.TAKEN_DOWN, ecode.RATE_LIMIT_REACHED]),
        'auth': ('Authorisation error, consider re-configuring the plugin', [ecode.INVALID_AUTH, ecode.AUTH_EXPIRED]),
        'contents': ('Illegal note contents', [ecode.ENML_VALIDATION])
    }


def errcode2name(err):
    name = ecode._VALUES_TO_NAMES.get(err.errorCode, "UNKNOWN")
    name = name.replace("_", " ").capitalize()
    return name


def err_reason(err):
    for reason, group in error_groups.values():
        if err.errorCode in group:
            return reason
    return "Unknown reason"


def explain_error(err):
    if isinstance(err, EDAMUserException):
        printError("Evernote error: [%s]\n\t%s" % (errcode2name(err), err.parameter))
        if err.errorCode in error_groups["contents"][1]:
            explanation = "The contents of the note are not valid.\n"
            msg = err.parameter.split('"')
            what = msg[0].strip().lower()
            if what == "element type":
                return explanation +\
                    "The inline HTML tag '%s' is not allowed in Evernote notes." %\
                    msg[1]
            elif what == "attribute":
                if msg[1] == "class":
                    return explanation +\
                        "The note contains a '%s' HTML tag "\
                        "with a 'class' attribute; this is not allowed in a note.\n"\
                        "Please use inline 'style' attributes or customise "\
                        "the 'inline_css' setting." %\
                        msg[3]
                else:
                    return explanation +\
                        "The note contains a '%s' HTML tag"\
                        " with a '%s' attribute; this is not allowed in a note." %\
                        (msg[3], msg[1])
            return explanation + err.parameter
        else:
            return err_reason(err)
    elif isinstance(err, EDAMSystemException):
        printError("Evernote error: [%s]\n\t%s" % (errcode2name(err), err.message))
        return "Evernote cannot perform the requested action:\n" + err_reason(err)
    elif isinstance(err, EDAMNotFoundException):
        printError("Evernote error: [%s = %s]\n\tNot found" % (err.identifier, err.key))
        return "Cannot find %s" % err.identifier.split('.', 1)[0]
    elif isinstance(err, gaierror):
        printError("Evernote error: [socket]\n\t%s" % str(err))
        return 'The Evernote services seem unreachable.\n'\
               'Please check your connection and retry.'
    else:
        printError("Evernote plugin error: %s" % str(err))
        return 'Evernote plugin error, please see the console for more details.\nThen contact developer at\n'\
               'https://github.com/bordaigorl/sublime-evernote/issues'


def printError(msg):
    print(msg)
    last_cmd, last_args, _ = sublime.active_window().active_view().command_history(-1)
    print("\tLast command: %s %s" % (last_cmd, last_args or {}))
    print("\tBEFORE SUBMITTING AN ISSUE (https://github.com/bordaigorl/sublime-evernote/issues):")
    print("\t  1. Enable the `debug` setting in your Evernote.sublime-settings file and try again. If the problem persists take a note of the output in the console.\n\t     Make sure you delete personal information (e.g. Developer Token) from the output before posting it in an issue.")
    print("\t  2. Check the wiki at https://github.com/bordaigorl/sublime-evernote/wiki")
    print("\t  3. Search for similar issues at https://github.com/bordaigorl/sublime-evernote/issues?q=is%3Aissue")
    print("\t(Evernote plugin v%s, ST %s, Python %s, %s %s%s)" % (
        EVERNOTE_PLUGIN_VERSION,
        sublime.version(),
        "%s.%s.%s" % sys.version_info[:3],
        sublime.platform(),
        sublime.arch(),
        ', debug' if DEBUG else '' ))


def async_do(f, progress_msg="Evernote operation", done_msg="", on_completion=None):
    if done_msg == "":
        done_msg = progress_msg + ': ' + "done!"
    status = {'done': False, 'i': 0}

    def do_stuff(s):
        try:
            f()
        except:
            pass
        finally:
            s['done'] = True
            if on_completion:
                on_completion()

    def progress(s):
        if s['done']:
            if done_msg is not None:
                sublime.status_message(done_msg)
        else:
            i = s['i']
            bar = "... [%s=%s]" % (' '*i, ' '*(7-i))
            sublime.status_message(progress_msg + bar)
            s['i'] = (i + 1) % 8
            sublime.set_timeout(lambda: progress(s), 100)

    sublime.set_timeout(lambda: progress(status), 0)
    sublime.set_timeout_async(lambda: do_stuff(status), 0)


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

    def get_shard_id(self, token=None):
        token_parts = (token or self.token()).split(":")
        id = token_parts[0][2:]
        return id

    def get_user_id(self, token=None):
        token_parts = (token or self.token()).split(":")
        id = token_parts[1][2:]
        return int(id, 16)

    def load_settings(self):
        global DEBUG
        self.settings = sublime.load_settings(EVERNOTE_SETTINGS)
        DEBUG = bool(self.settings.get('debug'))
        pygm_style = self.settings.get('code_highlighting_style')
        if pygm_style:
            EvernoteDo.MD_EXTRAS['fenced-code-blocks']['style'] = pygm_style
        if self.settings.get("code_friendly"):
            EvernoteDo.MD_EXTRAS['code-friendly'] = None
            html2text.EMPHASIS_MARK = "*"
        else:
            html2text.EMPHASIS_MARK = self.settings.get('emphasis_mark', html2text.EMPHASIS_MARK)
        if self.settings.get("wiki_tables"):
            EvernoteDo.MD_EXTRAS['wiki-tables'] = None
        if self.settings.get("gfm_tables"):
            EvernoteDo.MD_EXTRAS['tables'] = None
        css = self.settings.get("inline_css")
        if css is not None:
            for tag in css:
                css[tag] = css[tag].strip()
                if not css[tag].endswith(";"):
                    css[tag] = css[tag] + ";"
            EvernoteDo.MD_EXTRAS['inline-css'] = css
        self.md_syntax = self.settings.get("md_syntax")
        if not self.md_syntax:
            self.md_syntax = find_syntax("Evernote")
        html2text.UL_ITEM_MARK = self.settings.get('item_mark', html2text.UL_ITEM_MARK)
        html2text.STRONG_MARK = self.settings.get('strong_mark', html2text.STRONG_MARK)

    def message(self, msg):
        sublime.status_message(msg)

    def update_status_info(self, note, view=None):
        view = view or (self.view if hasattr(self, "view") else None)
        if not view:
            return
        info = "Note created %s, updated %s, %s attachments" % (
            datestr(note.created), datestr(note.updated), len(note.resources or []))
        view.set_status("Evernote-info", info)
        if view.file_name() is None and note.title is not None:
            view.set_name(self.settings.get("tab_prefix", "") + note.title)

    def connect(self, callback, **kwargs):
        self.message("initializing..., please wait...")

        def __connect(token, noteStoreUrl):
            if noteStoreUrl.startswith("https://") and not ssl:
                LOG("Not using SSL")
                noteStoreUrl = "http://" + noteStoreUrl[8:]
            self.settings.set("token", token)
            self.settings.set("noteStoreUrl", noteStoreUrl)
            sublime.save_settings(EVERNOTE_SETTINGS)
            callback(**kwargs)

        def __derive_note_store_url(token):
            id = self.get_shard_id(token)
            url = "www.evernote.com/shard/" + id + "/notestore"
            if ssl:
                url = "https://" + url
            else:
                url = "http://" + url
            return url

        def on_token(token):
            noteStoreUrl = self.settings.get("noteStoreUrl")
            if not noteStoreUrl:
                noteStoreUrl = __derive_note_store_url(token)
                p = self.window.show_input_panel(
                    "NoteStore URL (required):", noteStoreUrl,
                    lambda x: __connect(token, x),
                    None, None)
                p.sel().add(sublime.Region(0, p.size()))
            else:
                __connect(token, noteStoreUrl)

        token = self.token()
        noteStoreUrl = self.settings.get("noteStoreUrl")
        if not token or not noteStoreUrl:
            webbrowser.open_new_tab("https://www.evernote.com/api/DeveloperToken.action")
            self.window.show_input_panel(
                "Developer Token (required):", token or "",
                on_token, None, None)

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
            if self.settings.get("sort_notebooks"):
                notebooks.sort(key=lambda nb: nb.name)
        except Exception as e:
            sublime.error_message(explain_error(e))
            LOG(e)
            return []
        EvernoteDo._notebook_by_name = dict([(nb.name, nb) for nb in notebooks])
        EvernoteDo._notebook_by_guid = dict([(nb.guid, nb) for nb in notebooks])
        EvernoteDo._notebooks_cache = notebooks
        return notebooks

    def create_notebook(self, name):
        try:
            noteStore = self.get_note_store()
            notebook = Types.Notebook()
            notebook.name = name
            notebooks = noteStore.createNotebook(self.token(), notebook)
        except Exception as e:
            sublime.error_message(explain_error(e))
            LOG(e)
            return None
        EvernoteDo._notebooks_cache = None # To force notebook cache refresh
        return self.notebook_from_name(name)

    def get_note_link(self, guid):
        linkformat = 'evernote:///view/{userid}/{shardid}/{noteguid}/{noteguid}/'
        return linkformat.format(userid=self.get_user_id(), shardid=self.get_shard_id(), noteguid=guid)

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
            self.cache_all_tags()
        return EvernoteDo._tag_guid_cache[name]

    def cache_all_tags(self):
        tags = self.get_note_store().listTags(self.token())
        for tag in tags:
            EvernoteDo._tag_name_cache[tag.guid] = tag.name
            EvernoteDo._tag_guid_cache[tag.name] = tag.guid

    @staticmethod
    def clear_cache():
        EvernoteDo._noteStore = None
        EvernoteDo._notebook_by_name = None
        EvernoteDo._notebook_by_guid = None
        EvernoteDo._notebooks_cache = None
        EvernoteDo._tag_guid_cache = {}
        EvernoteDo._tag_name_cache = {}

    def populate_note(self, note, contents):
        if isinstance(contents, sublime.View):
            contents = contents.substr(sublime.Region(0, contents.size()))
        body = markdown2.markdown(contents, extras=EvernoteDo.MD_EXTRAS)

        wrapper_style = ''
        if 'inline-css' in EvernoteDo.MD_EXTRAS:
            if 'body' in EvernoteDo.MD_EXTRAS['inline-css']:
                wrapper_style = EvernoteDo.MD_EXTRAS['inline-css']['body']

        meta = body.metadata or {}
        content = '<?xml version="1.0" encoding="UTF-8"?>'
        content += '<!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd">'
        content += '<en-note style="%s">' % wrapper_style
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
            notebooks = self.get_notebooks()
            for nb in notebooks:
                if nb.name == meta["notebook"]:
                    note.notebookGuid = nb.guid
                    break
        return note


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

        try:
            if not self.token():
                self.connect(lambda **kw: self.do_run(edit, **kw), **kwargs)
            else:
                self.do_run(edit, **kwargs)
        except Exception as e:
            sublime.error_message('Evernote error:\n%s' % explain_error(e))


class EvernoteDoWindow(EvernoteDo, sublime_plugin.WindowCommand):

    def run(self, **kwargs):
        if DEBUG:
            from imp import reload
            reload(markdown2)
            reload(html2text)

        self.view = self.window.active_view()

        self.load_settings()

        try:
            if not self.token():
                self.connect(self.do_run, **kwargs)
            else:
                self.do_run(**kwargs)
        except Exception as e:
            sublime.error_message('Evernote error:\n%s' % explain_error(e))


class SendToEvernoteCommand(EvernoteDoText):

    def do_run(self, edit, **kwargs):
        self.do_send(**kwargs)

    def do_send(self, **args):
        noteStore = self.get_note_store()
        note = Types.Note()
        view = self.view

        if "title" in args:
            note.title = args["title"]
        if "notebook" in args:
            try:
                note.notebookGuid = self.notebook_from_name(args["notebook"]).guid
            except:
                note.notebookGuid = None
        if "tags" in args:
            note.tagNames = extractTags(args["tags"])

        on_send_completion = None
        if "on_completion" in args:
            on_send_completion = args["on_completion"]

        default_tags = args.get("default_tags", "")
        default_title = ""
        contents = ""
        clip = args.get("clip", False)
        if clip:
            if not view.has_non_empty_selection_region():
                sels = [sublime.Region(0, view.size())]
            else:
                sels = view.sel()
            import re
            INDENT = re.compile(r'^\s*', re.M)
            snippets = []
            for region in sels:
                if region.size() > 0:
                    lang = language_name(view.scope_name(region.begin()))
                    snippet = view.substr(region)
                    # deindent if necessary
                    strip = None
                    for m in INDENT.findall(snippet):
                        l = len(m)
                        if l <= (strip or l):
                            strip = l
                        if strip == 0:
                            break
                    # strip = min([len(m) for m in INDENT.findall(snippet)])
                    if strip > 0:
                        snippet = '\n'.join([line[strip:] for line in snippet.splitlines()])
                    snippets.append("```%s\n%s\n```" % (lang, snippet))
            contents = "\n\n".join(snippets) + "\n"
            if view.file_name():
                default_title = "Clip from "+os.path.basename(view.file_name())
        else:
            contents = view.substr(sublime.Region(0, view.size()))

        notebooks = self.get_notebooks()
        self.populate_note(note, contents)

        def on_cancel():
            self.message("Note not sent.")

        def choose_title():
            if not note.title:
                self.window.show_input_panel(
                    "Title (required):", default_title, choose_tags, None, on_cancel)
            else:
                choose_tags()

        def choose_tags(title=None):
            if title is not None:
                note.title = title
            if note.tagNames is None:
                self.window.show_input_panel(
                    "Tags (Optional):", default_tags, choose_notebook, None, on_cancel)
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
            async_do(lambda: __send_note_async(notebookGuid), "Sending note", on_completion=on_send_completion)

        def __send_note_async(notebookGuid):
            note.notebookGuid = notebookGuid

            LOG(note.title)
            LOG(note.tagNames)
            LOG(note.notebookGuid)
            LOG(note.content)

            try:
                self.message("Posting note, please wait...")
                cnote = noteStore.createNote(self.token(), note)
                if not clip:
                    set_view_metadata(view, cnote)
                    view.set_syntax_file(self.md_syntax)
                self.message("Successfully posted note: guid:%s" % cnote.guid, 10000)
                self.update_status_info(cnote)
            except EDAMUserException as e:
                args = dict(title=note.title, notebookGuid=note.notebookGuid, tags=note.tagNames)
                if e.errorCode == 9:
                    self.connect(self.do_send, **args)
                else:
                    if sublime.ok_cancel_dialog('Evernote complained:\n\n%s\n\nRetry?' % explain_error(e)):
                        self.connect(self.do_send, **args)
            except EDAMSystemException as e:
                sublime.error_message('Evernote error:\n%s' % explain_error(e))
            except Exception as e:
                sublime.error_message('Evernote plugin error %s' % e)

        choose_title()


class SaveEvernoteNoteCommand(EvernoteDoText):

    def do_run(self, edit, **args):
        note = Types.Note()
        noteStore = self.get_note_store()

        on_save_completion = None
        if "on_completion" in args:
            on_save_completion = args["on_completion"]

        note.title = self.view.settings().get("$evernote_title")
        note.guid = self.view.settings().get("$evernote_guid")

        self.populate_note(note, self.view)

        self.message("Updating note, please wait...")

        def __update_note():
            try:
                cnote = noteStore.updateNote(self.token(), note)
                set_view_metadata(self.view, cnote)
                self.message("Successfully updated note: guid:%s" % cnote.guid)
                self.update_status_info(cnote)
            except Exception as e:
                if sublime.ok_cancel_dialog('Evernote complained:\n\n%s\n\nRetry?' % explain_error(e)):
                    self.connect(self.__update_note)

        async_do(__update_note, "Updating note", on_completion=on_save_completion)

    def is_enabled(self, **kw):
        if self.view.settings().get("$evernote_guid", False):
            return True
        return False


DELETE_MSG = "You are about to delete '%s'.\nYour note will still be recoverable from the Trash.\nDo you want to proceed?"


class DeleteEvernoteNoteCommand(EvernoteDoText):

    def do_run(self, edit, guid=None, prompt=True):
        if guid is None:
            guid = self.view.settings().get("$evernote_guid")
        if guid:
            title = self.view.settings().get("$evernote_title", "Untitled")
            noteStore = self.get_note_store()
            if not prompt or sublime.ok_cancel_dialog(DELETE_MSG % title):
                noteStore.deleteNote(self.token(), guid)
                self.view.settings().set("$evernote_guid", None)
                self.view.settings().set("$evernote_modified", self.view.change_count())
                self.view.close()
        return

    def is_enabled(self, **kw):
        if self.view.settings().get("$evernote_guid", False):
            return True
        return False


class OpenEvernoteNoteCommand(EvernoteDoWindow):

    def do_run(self, note_guid=None, by_searching=None,
               from_notebook=None, with_tags=None,
               order=None, ascending=None, max_notes=None, **kwargs):
        notebooks = self.get_notebooks()

        search_args = {}

        order = order or self.settings.get("notes_order", "default").upper()
        search_args['order'] = Types.NoteSortOrder._NAMES_TO_VALUES.get(order.upper())  # None = default
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

            if len(notes) == 1 and self.settings.get("open_single_result"):
                on_note(0)
                return

            if show_notebook:
                menu = ["[%s] » %s" % (self.notebook_from_guid(note.notebookGuid).name, note.title) for note in notes]
                # menu = [[note.title, self.notebook_from_guid(note.notebookGuid).name] for note in notes]
            else:
                menu = [note.title for note in notes]
            self.window.show_quick_panel(menu, on_note)

        def on_notebook(notebook):
            if notebook < 0:
                return
            search_args['notebookGuid'] = notebooks[notebook].guid
            notes = self.find_notes(search_args, max_notes)
            async_do(lambda: notes_panel(notes), "Fetching notes list", done_msg=None)

        def do_search(query):
            self.message("Searching notes...")
            search_args['words'] = query
            async_do(lambda: notes_panel(self.find_notes(search_args, max_notes), True), "Fetching notes list", done_msg=None)

        if note_guid:
            self.open_note(note_guid, **kwargs)
            return

        if by_searching:
            if isinstance(by_searching, str):
                do_search(by_searching)
            else:
                p = self.window.show_input_panel("Enter search query:", "", do_search, None, None)
                if isinstance(by_searching, dict):
                    p.run_command("insert_snippet", {"contents": by_searching.get("snippet", "")})
            return

        if from_notebook or with_tags:
            notes_panel(self.find_notes(search_args, max_notes), not from_notebook)
        elif len(notebooks) == 1:
            on_notebook(0)
        else:
            if self.settings.get("show_stacks", True):
                menu = ["%s » %s" % (nb.stack, nb.name) if nb.stack else nb.name for nb in notebooks]
            else:
                menu = [nb.name for nb in notebooks]
            self.window.show_quick_panel(menu, on_notebook)

    def find_notes(self, search_args, max_notes=None):
        return self.get_note_store().findNotesMetadata(
            self.token(),
            NoteStore.NoteFilter(**search_args),
            None, max_notes or self.settings.get("max_notes", 100),
            NoteStore.NotesMetadataResultSpec(includeTitle=True, includeNotebookGuid=True)).notes

    def open_note(self, guid, convert=True, **unk_args):
        async_do(lambda: self.do_open_note(guid, convert, **unk_args), "Retrieving note")

    def do_open_note(self, guid, convert=True, **unk_args):
        try:
            noteStore = self.get_note_store()
            note = noteStore.getNote(self.token(), guid, True, False, False, False)
            nb_name = self.notebook_from_guid(note.notebookGuid).name
            LOG(note.content)
            LOG(note.guid)
            if convert:
                # tags = [noteStore.getTag(self.token(), guid).name for guid in (note.tagGuids or [])]
                # tags = [self.tag_from_guid(guid) for guid in (note.tagGuids or [])]
                tags = noteStore.getNoteTagNames(self.token(), note.guid)
                meta = metadata_header(note.title, tags, nb_name)
                builtin = note.content.find(SUBLIME_EVERNOTE_COMMENT_BEG, 0, 150)
                if builtin >= 0:
                    try:
                        builtin_end = note.content.find(SUBLIME_EVERNOTE_COMMENT_END, builtin)
                        bmdtxt = note.content[builtin+len(SUBLIME_EVERNOTE_COMMENT_BEG):builtin_end]
                        mdtxt = b64decode(bmdtxt.encode('utf8')).decode('utf8')
                        parts = extract_metadata(mdtxt)
                        if parts["metadata"]:
                            if parts["metadata"].get("title") == note.title and \
                               "tags" in parts["metadata"] and \
                               set(parts["metadata"].get("tags")) == set(tags) and \
                               parts["metadata"].get("notebook") == nb_name:
                                meta = ""
                            else:
                                LOG("Overridding metadata")
                                mdtxt = parts["contents"]
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

                if unk_args.get('open_new_file', True) == False:
                    newview = self.window.active_view()
                else:
                    newview = self.window.new_file()
                set_view_metadata(newview, note, False)
                syntax = self.md_syntax
                note_contents = meta+mdtxt
            else:
                newview = self.window.new_file()
                syntax = find_syntax("XML")
                note_contents = note.content
            newview.set_syntax_file(syntax)
            newview.set_scratch(True)
            replace_view_text(newview, note_contents)
            self.message('Note "%s" opened!' % note.title)
            self.update_status_info(note, newview)
            note_is_current(newview)
        except Exception as e:
            sublime.error_message(explain_error(e))


class AttachToEvernoteNote(OpenEvernoteNoteCommand):

    def open_note(self, guid, insert_in_content=True, filename=None, prompt=False, **unk_args):
        import hashlib, mimetypes
        if filename is None:
            view = self.view
            if view is None:
                sublime.error_message("Evernote plugin could not open the file you specified!")
                return
            filename = view.file_name() or ""
            contents = view.substr(sublime.Region(0, view.size())).encode('utf8')
        else:
            filename = os.path.abspath(filename)
            if prompt:
                self.window.show_input_panel(
                    "Filename of attachment: ", filename,
                    lambda x: self.open_note(guid, insert_in_content, filename, prompt=False, **unk_args),
                    None, None)
                return
            try:
                with open(filename, 'rb') as content_file:
                    contents = content_file.read()
            except Exception as e:
                sublime.error_message("Evernote plugin could not open the file you specified!")
                print(e)
                return
        try:
            noteStore = self.get_note_store()
            note = noteStore.getNote(self.token(), guid, True, False, False, False)
            mime = mimetypes.guess_type(filename)[0]
            LOG(mime)
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
                    '<en-media type="%s" hash="%s"/></en-note>' % (mime, h.hexdigest())
            note.resources = resources
            def do():
                noteStore.updateNote(self.token(), note)
                self.message("Successfully attached to note '%s'" % note.title)
            async_do(do, "Uploading attachment")
        except Exception as e:
            sublime.error_message(explain_error(e))

    def is_enabled(self, insert_in_content=True, filename=None, **unk):
        return filename is not None or self.window.active_view() is not None


class InsertLinkToEvernoteNote(OpenEvernoteNoteCommand):

    def open_note(self, guid, **unk_args):
        noteStore = self.get_note_store()
        note = noteStore.getNote(self.token(), guid, False, False, False, False)
        title = note.title
        link = self.get_note_link(guid)
        mdlink = '[{}]({})'.format(title, link)
        insert_to_view(self.view, mdlink)

    def is_enabled(self, **kw):
        return bool(self.window.active_view().settings().get('$evernote', False))


# Note that this regex needs to work with both Python as well as Sublime.
_EVERNOTE_LINK_REGEX = \
    '\[(.+)\]' \
    '\(' \
    'evernote:///view/' \
    '\d+/' \
    's\d+/' \
    '([0-9a-f-]+)/' \
    '[0-9a-f-]+/' \
    '\)'


class OpenLinkedEvernoteNote(EvernoteDoText):

    def do_run(self, edit):
        guid = self.find_note_link_guid()
        if guid is None:
            return

        LOG('Found link to note', guid)
        self.view.window().run_command('open_evernote_note', {'note_guid': guid})

    def find_note_link_guid(self):
        if len(self.view.sel()) != 1:
            return None

        # Search a reasonable range for the link
        offset = 500
        begin = max(0, self.view.sel()[0].a - offset)
        end = min(self.view.size(), self.view.sel()[0].a + offset)
        relpos = self.view.sel()[0].a - begin
        text = self.view.substr(sublime.Region(begin, end))

        for m in re.finditer(_EVERNOTE_LINK_REGEX, text, re.IGNORECASE):
            if m.start() >= relpos:
                break
            if m.end() <= relpos:
                continue

            return m.group(2)

        return None

    def is_visible(self):
        return bool(self.view.settings().get('$evernote', False))

    def is_enabled(self, **kw):
        return (self.view.settings().get('$evernote', False) and
                self.find_note_link_guid() is not None)


class ListLinkedEvernoteNotes(EvernoteDoText):

    def do_run(self, edit):
        links = []
        self.view.find_all(_EVERNOTE_LINK_REGEX, sublime.IGNORECASE, '$0', links)

        if not links:
            self.message('Could not find any links in the current note')
            return

        links = [re.match(_EVERNOTE_LINK_REGEX, l, re.IGNORECASE).groups() for l in links]
        linktitles, linkguids = (list(l) for l in zip(*links))

        def open_link(i):
            if i == -1:
                return

            guid = linkguids[i]
            LOG('Opening link to note', guid)
            self.view.window().run_command('open_evernote_note', {'note_guid': guid})

        self.view.window().show_quick_panel(linktitles, open_link)

    def is_enabled(self, **kw):
        return bool(self.view.settings().get('$evernote', False))


class ViewInEvernoteClientCommand(EvernoteDoText):

    def do_run(self, edit):
        notelink = self.get_note_link(self.view.settings().get("$evernote_guid"))
        LOG('Launching Evernote client with link', notelink)
        open_file_with_app(notelink)

    def is_enabled(self, **kw):
        return bool(self.view.settings().get("$evernote_guid", False))


class RevertToEvernoteCommand(OpenEvernoteNoteCommand):

    def do_run(self, **kwargs):
        open_new_file = False
        if self.view.change_count() > self.view.settings().get("$evernote_modified"):
            answer = sublime.yes_no_cancel_dialog("Note has been modified and reverting to version from Evernote will replace it's contents. Do you want to replace?", "Replace", "Open in new tab")
            if answer == sublime.DIALOG_NO:
                open_new_file = True
            elif answer == sublime.DIALOG_CANCEL:
                return

        note_guid = self.view.settings().get("$evernote_guid")
        self.open_note(note_guid, open_new_file=open_new_file, **kwargs)
        self.message("Loading note, please wait...")

    def is_enabled(self):
        if self.window.active_view().settings().get("$evernote_guid", False):
            return True
        return False

class EvernoteInsertAttachment(EvernoteDoText):

        def do_run(self, edit, insert_in_content=True, filename=None, prompt=False):
            import hashlib, mimetypes, urllib
            view = self.view
            if filename is None or prompt:
                view.window().show_input_panel(
                    "Filename or URL of attachment: ", filename or "",
                    lambda x: view.run_command(
                        "evernote_insert_attachment",
                        {'insert_in_content': insert_in_content, "filename": x, "prompt": False}),
                    None, None)
                return
            filename = filename.strip()
            attr = {}
            mimet = None
            try:
                if os.path.isfile(filename):
                    datafile = os.path.expanduser(filename)
                    with open(datafile, 'rb') as content_file:
                        filecontents = content_file.read()
                    attr = {"fileName": os.path.basename(datafile)}
                else:
                    # download
                    response = urllib.request.urlopen(filename)
                    filecontents = response.read()
                    attr = {"sourceURL": filename}
                    mimet = response.info().get_content_type()
            except Exception as e:
                sublime.error_message(
                    "Evernote plugin has troubles locating the specified file/URL.\n" +
                    explain_error(e))
                return

            def upload_async():
                try:
                    guid = self.view.settings().get("$evernote_guid")
                    noteStore = self.get_note_store()
                    note = noteStore.getNote(self.token(), guid, False, False, False, False)
                    mime = mimet or mimetypes.guess_type(filename)[0] or "application/octet-stream"
                    h = hashlib.md5(filecontents)
                    attachment = Types.Resource(
                        # noteGuid=guid,
                        mime=mime,
                        data=Types.Data(body=filecontents, size=len(filecontents), bodyHash=h.digest()),
                        attributes=Types.ResourceAttributes(attachment=not insert_in_content, **attr))
                    resources = note.resources or []
                    resources.append(attachment)
                    note.resources = resources
                    noteStore.updateNote(self.token(), note)
                    if insert_in_content:
                        tag = '<en-media type="%s" hash="%s"/>' % (mime, h.hexdigest())
                        view.run_command('insert', {'characters': tag})
                        sublime.set_timeout(lambda: view.run_command("save_evernote_note"), 10)
                except Exception as e:
                    sublime.error_message(
                        "Evernote plugin cannot insert the attachment.\n" +
                        explain_error(e))

            async_do(upload_async, "Uploading attachment")

        def is_enabled(self, **kw):
            if self.view.settings().get("$evernote_guid", False):
                return True
            return False


def open_file_with_app(filepath):
    import subprocess
    if sublime.platform() == "osx":
        subprocess.call(('open', filepath))
    elif sublime.platform() == "windows":
        os.startfile(filepath)
    elif sublime.platform() == "linux":
        subprocess.call(('xdg-open', filepath))


def hashstr(h):
    return ''.join(["%x" % b for b in h])


class EvernoteShowAttachments(EvernoteDoText):

    def do_run(self, edit):
        guid = self.view.settings().get("$evernote_guid")
        noteStore = self.get_note_store()
        note = noteStore.getNote(self.token(), guid, False, False, False, False)
        resources = note.resources or []
        menu = [[r.attributes.fileName or r.attributes.sourceURL or
                 ("Unnamed %s" % (r.mime or "")),
                 "hash: %s" % hashstr(r.data.bodyHash)]
                for r in resources]

        def on_done(i):
            async_do(lambda: on_done_async(i), "Fetching Attachment")

        def on_done_async(i):
            if i >= 0:
                import tempfile, mimetypes
                try:
                    contents = noteStore.getResource(
                        self.token(), note.resources[i].guid,
                        True, False, False, False).data.body
                    mime = resources[i].mime or "application/octet-stream"
                    _, tmp = tempfile.mkstemp(mimetypes.guess_extension(mime) or "")
                    mime = mime.split("/")[0]
                    with open(tmp, 'wb') as tmpf:
                        tmpf.write(contents)
                    if mime in ["text", "image"]:
                        aview = self.view.window().open_file(tmp)
                        aview.set_read_only(True)
                        # aview.set_scratch(True)
                        # aview.set_name(menu[i][0])
                    else:
                        open_file_with_app(tmp)
                except Exception as e:
                    sublime.error_message(
                        "Unable to fetch the attachment.\n%s" % explain_error(e))

        if menu:
            self.view.window().show_quick_panel(menu, on_done)
        else:
            self.message("Note has no attachments")

    def is_enabled(self, **kw):
        if self.view.settings().get("$evernote_guid", False):
            return True
        return False


class EvernoteDeleteAttachment(EvernoteDoText):

        def do_run(self, edit, attachment_hash=None, attachment_index=None):
            guid = self.view.settings().get("$evernote_guid")
            noteStore = self.get_note_store()
            note = noteStore.getNote(self.token(), guid, False, False, False, False)

            if attachment_hash or attachment_index:
                try:
                    if attachment_hash is None:
                        attachment_hash = hashstr(note.resources[attachment_index].data.bodyHash)
                    if attachment_index is None:
                        for r in range(len(note.resources)):
                            if hashstr(note.resources[r].data.bodyHash) == attachment_hash:
                                attachment_index = r
                        if attachment_index is None:
                            sublime.error_message("Attachment not found!")
                            return

                    note.resources.pop(attachment_index)
                    noteStore.updateNote(self.token(), note)

                    regions = self.view.find_all(
                        r'<en-media [^>]*hash\s*=\s*["\']'+attachment_hash+'["\'][^>]*/>',
                        sublime.IGNORECASE)
                    for region in reversed(regions):
                        self.view.erase(edit, region)

                except Exception as e:
                    sublime.error_message(
                        "Unable to delete the attachment.\n%s" % explain_error(e))
            else:
                resources = note.resources or []
                menu = [[r.attributes.fileName or r.attributes.sourceURL or
                         ("Unnamed %s" % (r.mime or "")),
                         "hash: %s" % hashstr(r.data.bodyHash)]
                        for r in resources]

                def on_done(i):
                    async_do(lambda: on_done_async(i), "Deleting Attachment")

                def on_done_async(i):
                    if i >= 0:
                        rhash = hashstr(note.resources[i].data.bodyHash)
                        # Not the most efficient way (downloads note's metadata twice)
                        # but this way we will have a valid edit object for erasing
                        self.view.run_command(
                            "evernote_delete_attachment",
                            {"attachment_hash": rhash, "attachment_index": i})
                if menu:
                    self.view.window().show_quick_panel(menu, on_done)
                else:
                    self.message("Note has no attachments")

        def is_enabled(self, **kw):
            if self.view.settings().get("$evernote_guid", False):
                return True
            return False


class ViewInEvernoteWebappCommand(sublime_plugin.TextCommand):

    def run(self, edit):
        url = self.view.settings().get("noteStoreUrl")[0:-9] + "view/%s"
        webbrowser.open_new_tab(url % self.view.settings().get("$evernote_guid"))

    def is_enabled(self, **kw):
        if self.view.settings().get("$evernote_guid", False):
            return True
        return False


META_SNIPPET = """\
---
title: $3
notebook: $1
tags:$2
---

$0

"""

def try_loading_template(template, complain=True):
    try:
        contents = sublime.load_resource(template)
        return contents
    except Exception:
        if complain:
            sublime.error_message("Template file '%s' for new Evernote note not found.")
        return None


class NewEvernoteNoteCommand(EvernoteDo, sublime_plugin.WindowCommand):

    def run(self, contents=None, template=None):
        self.load_settings()
        if contents is None:
            template = template or self.settings.get("default_template", None)
            contents = try_loading_template(template, False)
            if contents is None and template is not None:
                templates = sublime.find_resources(template)
                if len(templates) > 1:

                    def on_choice(i):
                        if i >= 0:
                            contents = try_loading_template(templates[i])
                            if contents is not None:
                                self.window.run_command("new_evernote_note", {"contents": contents})

                    self.window.show_quick_panel(templates, on_choice)
                    return

                elif len(templates) == 1:
                    contents = try_loading_template(templates[0])
            contents = contents or META_SNIPPET
        view = self.window.new_file()
        view.set_syntax_file(self.md_syntax)
        view.settings().set("$evernote", True)
        view.set_status("Evernote-info", "Send to evernote to save your edits")
        view.set_scratch(True)
        view.set_name(self.settings.get("tab_prefix", "") + "Unsaved note")
        view.run_command("insert_snippet", {"contents": contents})
        if self.settings.get('evernote_autocomplete'):
            sublime.set_timeout(lambda: view.run_command("auto_complete"), 10)


class ReconfigEvernoteCommand(EvernoteDoWindow):

    def run(self):
        self.window = sublime.active_window()
        self.settings = sublime.load_settings(EVERNOTE_SETTINGS)
        self.settings.erase("token")
        self.settings.erase("noteStoreUrl")
        self.clear_cache()
        self.connect(lambda: True)


class CreateNotebookCommand(EvernoteDoWindow):

    def run(self):
        self.view = self.window.active_view()
        self.settings = sublime.load_settings(EVERNOTE_SETTINGS)

        def on_notebook(notebook):
            if not notebook:
                sublime.error_message("Notebook name is required")
            else:
                new_notebook = self.create_notebook(notebook)
                if new_notebook:
                    self.message("Newly created notebook: %s" % notebook)

        self.window.show_input_panel(
            "Notebook name (required):", "",
            on_notebook, None, None)



class ClearEvernoteCacheCommand(sublime_plugin.WindowCommand):

    def run(self):
        EvernoteDo.clear_cache()
        LOG("Cache cleared!")


class ReplaceViewTextCommand(sublime_plugin.TextCommand):
    def run(self, edit, characters):
        self.view.erase(edit, sublime.Region(0, self.view.size()))
        self.view.insert(edit, 0, characters)
        self.view.run_command("move_to", {"to": "bof", "extend": False})


class EvernoteListener(EvernoteDo, sublime_plugin.EventListener):

    settings = {}

    def on_post_save(self, view):
        if self.settings.get('update_on_save'):
            view.run_command("save_evernote_note")

    def on_pre_close(self, view):
        if self.settings.get("warn_on_close") and \
           view and view.settings().get("$evernote") and \
           view.change_count() > view.settings().get("$evernote_modified", 0):
            # There is no API to cancel the closing of a view
            # so we let Sublime close it but clone it first and then ask the user.
            choices = ["Close and discard changes", "Save to Evernote and close"]
            view.window().run_command("clone_file")
            cloned = view.window().active_view()
            if not cloned:
                return

            guid = view.settings().get("$evernote_guid")

            def on_completion():
                note_is_current(cloned)
                cloned.close()

            def on_choice(i):
                if i == 1:
                    if guid:
                        SaveEvernoteNoteCommand(cloned).run(None, on_completion=on_completion)
                    else:
                        SendToEvernoteCommand(cloned).run(None, on_completion=on_completion)

                elif i == 0:
                    on_completion()

            cloned.window().show_quick_panel(choices, on_choice)

    def on_query_context(self, view, key, operator, operand, match_all):
        if key == "evernote_note":
            res = view.settings().get("$evernote", False)
            if (operator == sublime.OP_NOT_EQUAL) ^ (not operand):
                res = not res
            return res
        elif key == "evernote_has_guid":
            res = bool(view.settings().get("$evernote_guid"))
            if (operator == sublime.OP_NOT_EQUAL) ^ (not operand):
                res = not res
            return res
        return None

    first_time = True

    def on_query_completions(self, view, prefix, locations):
        if not self.settings.get('evernote_autocomplete'):
            return
        loc = locations[0]
        if not view.scope_name(loc).startswith("text.html.markdown.evernote meta.metadata.evernote"):
            return None
        if self.first_time:
            self.cache_all_tags()
            self.first_time = False

        line = view.substr(view.line(loc)).lstrip()
        if line.startswith("tags"):
            return [[tag, tag] for tag in EvernoteDo._tag_name_cache.values() if tag.startswith(prefix)]
        elif line.startswith("notebook"):
            return [[nb.name, nb.name] for nb in self.get_notebooks() if nb.name.startswith(prefix)]
        return None


def plugin_loaded():
    EvernoteListener.load_settings(EvernoteListener)
