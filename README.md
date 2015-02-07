Evernote for Sublime Text
=========================

[![Join the chat at https://gitter.im/bordaigorl/sublime-evernote](https://badges.gitter.im/Join%20Chat.svg)](https://gitter.im/bordaigorl/sublime-evernote?utm_source=badge&utm_medium=badge&utm_campaign=pr-badge&utm_content=badge)

[Sublime Text](http://www.sublimetext.com/3) plugin for [Evernote](http://www.evernote.com).

This package is based on [SublimeEvernote](https://github.com/jamiesun/SublimeEvernote) for ST2 but is only supported on ST3 and adds many new features.

To start using it install it from Package Control and type "Evernote" on the Command Pallette (<kbd>ctrl+shift+p</kbd>). See [First Use](#first-use) for linking the plugin to your account.

# Main Features

 * **Send a note to Evernote:** converts the markdown document in the current view into rich text and sends it to your Evernote. You will be able to choose a title, tags and the notebook where to store it.
 * **Open a note from Evernote**: shows panels to choose a note from a notebook, converts it to markdown and presents it in a view.
 * **Update note**: when editing the markdown of an opened note you can save it back to Evernote (again in rich text).
 * **Full two-way metadata support**: you can set and change the note's title, notebook and tag just by providing a YAML metadata header in your markdown source.
 * **Attachments**: can insert, list and open attachments.
 * **Clip to note**: saves the current selection as code snippets to a new note.

See [Commands](#commands) and the [wiki] for details.


## What's new

**v2.5.4**

 + Bugfix: solves a problem in setting a new token (see #48)
 + Added a `debug` flag in settings

**v2.5.3**

 + Added links to notes management (thanks to @rahul-ramadas, see #36)
 + Open note in Client command, tested on Windows only (thanks to @rahul-ramadas)
 + Bugfixes: better error messages, correct handling of unnamed attachments

**v2.5.2**

 + Added attachments management: list, open, insert attachments from file or url (solves #24)
 + Notebook list can show stack names and be sorted (#30, thanks @danielfrg)
 + Added `wiki_tables` setting to enable Wiki-style tables syntax (solves #18)
 + Now notes in webapp are opened in default browser (solves #22)
 + Configuration asks for noteStoreUrl so the plugin works properly for yinxiang.com (solves #20)

**v2.5.1**

 + Bugfix: metadata autocomplete now loads settings correctly 

**v2.5.0**

 + [Search note](#search-note) command added
 + [Attach](#attach-to-note) command added
 + [Clip](#clip-to-note) command added
 + [View in WebApp](#view-note-in-webapp) command added
 + Autocomplete in metadata block
 + Unicode chars in tags properly displayed
 + Creation and update dates are displayed in status

# Installation

The Evernote plugin can be installed using Package Control.
See the [wiki] for detailed instructions.

# Usage

## First use

When you first run this package from the command palette, it will launch a browser window with your Evernote developer token. Copy the token and paste it into the prompt at the bottom of your Sublime window. Sublime will store the authentication data in `Sublime Text 3/Packages/User/Evernote.sublime-settings`.

If you need to reconfigure the plugin go to `Preferences > Package Settings > Evernote` and select `Reconfigure Authorisation` or go to

`Command Palette` > `Evernote: Reconfigure`


> **PLEASE NOTE**
> 
> The authentication method makes use of the Developer Token which is unique to your account and grants read-write access to your Evernote.
> This token will be saved in your user settings in the `Evernote.sublime-settings` file so make sure you do not share this publicly! 


## Commands

The plugin does not install keymaps, if you wish you may add a variation of the following to your user keymaps:

```
{ "keys": ["super+e"], "command": "show_overlay", "args": {"overlay": "command_palette", "text": "Evernote: "} },
{ "keys": ["ctrl+e", "ctrl+s"], "command": "send_to_evernote" },
{ "keys": ["ctrl+e", "ctrl+o"], "command": "open_evernote_note" },
{ "keys": ["ctrl+e", "ctrl+u"], "command": "save_evernote_note" },
```

you can also overwrite the standard "save" bindings for Evernote notes as follows:

```
{ "keys": ["ctrl+s"], "command": "save_evernote_note", "context": [{"key": "evernote_note"}] },
{ "keys": ["ctrl+s"], "command": "send_to_evernote", "context": [{"key": "evernote_note", "operator": "equal", "operand": false}, {"key": "selector", "operator": "equal", "operand": "text.html.markdown.evernote"}] },
```

you would still be able to save the note as a file by using the `File > Save` menu. 

You can also restrict your bindings to views showing Evernote notes by adding

    "context": [{"key": "evernote_note"}]

### Send to Evernote

`Command Palette` > `Evernote: Send to Evernote`

This will create a new note containing the HTML version of the markdown code of your active view.
You will be able to specify the title, tags and notebook either from panels or with a metadata block ([see below](#metadata))

### Open Note

`Command Palette` > `Evernote: Open Evernote Note`

This will open a panel from which you can select a notebook and a note in it.
The selected note will be converted in markdown format and opened in a view.
This command only handles the main contents of the note and ignores the attachments, but existing attachments will be left as they are.

For more details about the parameters of this command see the [wiki](https://github.com/bordaigorl/sublime-evernote/wiki/The-Open-Note-Command).

### Search Note

`Command Palette` > `Evernote: Search Note`

You will be presented with a prompt where you can write a query in the Evernote query language [documented here](http://dev.evernote.com/doc/articles/search_grammar.php).
A panel will show the search results from which you can select a note.
The selected note will be converted in markdown format and opened in a view.

For more details about the parameters of this command see the [wiki].

### Update Note

`Command Palette` > `Evernote: Update Evernote Note`

When the current view is associated with an Evernote note (maybe because you just sent it to Evernote or because it is an opened note) you can update the note with this command.
The [metadata](#metadata) will be updated according to the metadata block and attachments stored in the original Evernote note will be left alone.

### Attachments

`Command Palette` > `Evernote: Attach current file to a note`

This will open a pallette from which you can select a notebook and a note in it.
The currently opened file will then be attached to the selected note.
Existing attachments of the selected note will remain untouched.

`Command Palette` > `Evernote: Insert Attachment Here`

Asks for a path or URL and inserts it as an attachment to the current note.
If an URL is provided, the file would be downloaded and uploaded to Evernote.
**Please Note**: for the time being Sublime Text will freeze during the download/upload operation for large files. Just wait until the transfer is complete.

`Command Palette` > `Evernote: Show Attachments`

The command will open a pallette listing all the attachments of the current note.
If one is selected it will be downloaded and displayed.
The download will be done asynchronously as it may take some time for heavy files.

### Clip as new Note

`Command Palette` > `Evernote: Clip to Evernote as a new note`

This command will take the current selections, format them as highlighted code snippets, put them in a new note, letting you choose its title, tags and notebook.

### Links to notes

`Command Palette` > `Evernote: List linked notes`

This command shows a list of links to notes present in the currently opened note, if any. Selecting an item in the list will open the note in a new view.

`Command Palette` > `Evernote: Insert link to a note`

Lets you select a note and inserts a link to it in the currently opened one.

### View note in WebApp/Client

`Command Palette` > `Evernote: View note in WebApp`

This command will open the currently opened note in Evernote's WebApp in a browser. From there you can view it, share it or continuing editing it from the WebApp's editor. You may need to login before being able to view the note.

`Command Palette` > `Evernote: View note in Evernote client`

This command will open the currently opened note in your local Evernote client, if installed.

## Markdown

You can use Markdown to write notes but there are some limitations due to Evernote's formats. For example, `class` and `id` are forbidden attributes in Evernote notes so the Markdown converter has been modified to never output them and raw HTML cannot contain them. If you write illegal content the plugin will display a dialog showing the reason why Evernote is complaining.

Please see the [wiki documentation](https://github.com/bordaigorl/sublime-evernote/wiki/Supported-Markdown) for more details.

## Metadata

A markdown source can start with a metadata block like the following:

```yaml
---
title: My Note's Title
tags: misc, sublime
notebook: My Notebook 
---
```

When sending or updating the note, the plugin will extract this metadata and set/change it accordingly. When such header is incomplete or missing, when sending the note to Evernote the plugin will ask for input for the missing fields.

The `tags` field can be an unquoted list or a json list such as `["my long tag", "tag2"]`.

If the `evernote_autocomplete` is true, the list of notebooks and tags will be offered as autocompletion in the metadata block.

**PLEASE NOTE**: the format for the metadata is currently rather restricted and it is just a small subset of YAML. The only recognised keys are `title`, `tags` and `notebook`, the others will be ignored and can be discarded (for example if you edit the note from other clients). 

# Equations

While equations are not natively supported by Evernote, you can embed them as images. The [Insert Equation](https://github.com/bordaigorl/sublime-inserteq) plugin can be used to ease their insertion into your Markdown note.

# Settings

The `Evernote.sublime-settings` can be accessed from `Preferences > Package Settings > Evernote`.

The two settings `token` and `noteStoreUrl` are set by the plugin in the [first use](#first-use).

The following settings can be customised:

Setting                   | Purpose
--------------------------|------------------------
`md_syntax`               | a string pointing to a `tmLanguage` file which you want to associate with notes opened from Evernote.
`inline_css`              | a dictionary associating some HTML element names to inline CSS styles; currently the only elements that can be styled in this way are: `pre`, `code`, `h1`, `hr`, `blockquote` and `sup`. Additionally `footnotes` can be associated to some style for the `div` containing the footnotes at the end of the note. The markdown of a note can contain (almost) arbitrary HTML blocks *but* Evernote only accepts a subset of the elements and attributes (`class` and `id` are disallowed). See [here](http://dev.evernote.com/doc/articles/enml.php) for details.
`code_highlighting_style` | a pygments style among `autumn`, `default`, `github`, `monokai`, `perldoc`, `vim`, `borland`, `emacs`, `igor`, `murphy`, `rrt`, `vs`,   `bw`, `friendly`, `native`, `tango`, `xcode`,   `colorful`, `fruity`, `manni`, `pastie`, `trac`.
`code_friendly`           | if `true` the `code-friendly` extra of markdown2 is enabled
`evernote_autocomplete`   | when this setting is true, suggestions will be offered for autocompletion of the `notebook` and `tags` fields in metadata. Default is true.
`notes_order`             | how to sort the notes in the panels; possible values: `created`, `updated`, `relevance`, `update_sequence_number`, `title`. Set the `notes_order_ascending` setting to `true` to reverse the selected order.
`max_notes`               | maximum number of notes in a panel; default is 100.
`update_on_save`          | when this setting is true, saving a file containing a note will also update (overwriting it) the online version. Default is false.
`sort_notebooks`          | sorts notebooks alphabetically in pallette
`show_stacks`             | shows the stack of notebooks in pallette
`debug`                   | enables logging in the console


# Acknowledgements

The current maintainer is [bordaigorl].

If you like this plugin and would like to support its development please consider donating through a [paypal donation](https://www.paypal.com/cgi-bin/webscr?cmd=_s-xclick&hosted_button_id=JFWLSUZYXUHAQ) or using [gittip](https://www.gittip.com/bordaigorl/).

If you would like to contribute, please see [CONTRIBUTING].

The plugin has been made possible by the contribution of several people:

 * Current maintainer and new features: [bordaigorl]
 * Original ST2 Plugin:
   [jamiesun](https://github.com/jamiesun/SublimeEvernote)
 * Port to ST3:
     - [rekotan](https://github.com/rekotan/SublimeEvernote)
     - [timlockridge](https://github.com/timlockridge/SublimeEvernote)
 * Other contributors:
   [rahul-ramadas](https://github.com/rahul-ramadas),
   [mwcraig](https://github.com/mwcraig),
   [rayou](https://github.com/rayou),
   [dimfeld](https://github.com/dimfeld),
   [paki](https://github.com/paki),
   [zsytssk](https://github.com/zsytssk),
   [metalbrick](https://github.com/metalbrick),
   [danielfrg](https://github.com/danielfrg).

If you think your name should be here, let us know!

Libraries (some adapted to work with Evernote formats):

 * Markdown2 converter: [trentm](https://github.com/trentm/python-markdown2/)
 * HTML2Markdown: [Aaron Swartz](https://github.com/aaronsw/html2text)
 * Evernote API: <https://github.com/evernote/evernote-sdk-python>


[CONTRIBUTING]: <CONTRIBUTING.md>
[wiki]: <https://github.com/bordaigorl/sublime-evernote/wiki/>
[bordaigorl]: <https://github.com/bordaigorl>