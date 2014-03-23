# Commands to be implemented

Always available:

+ New empty note
+ Define a unified restricted format for metadata:
    * in Evernote.tmLanguage
    * in markdown2
    * in sublime_evernote
+ **Clip to note**: send *selection* to Evernote.
  This would not set the `$evernote` flag, always ask for notebook/note name/tags and detect language of selection: if it's markdown, convert, if it's html send as it is, otherwise pygmentize and send.
+ **Attach to note**: prompt the user to select note, then attach a new resource to it with the contents of the current view.

When view shows a note:

+ Save/Upload as new note (send+open)
+ Save/Upload Note
- Diff local note with online version (approx on the markdown!)

- Autocomplete tags/notebooks
    + In metadata block (detect scope?)
    + trigger autocomplete when prefix is `tags:` and `notebook:`

- Status messages

# Highlighting invalid tags/attributes

Example: `<style>...<div id="...">` would mark them as invalid.
However this requires copying the Markdown language modifying only some parts.
Not very modular. Enough?

# Event Listeners

## On Load

- Parse metadata, set flag to signal this is a note
- If `download_on_load` redownload

## On Save

+ If view is a note and `upload_on_save` save to Evernote

## On query context

+ Just check for a note guid in metadata or in view settings

## On query completions

    def on_query_completions(self, view, prefix, locations):