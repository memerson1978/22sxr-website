# Booking engine: letter template

`letter-template.docx` is the layout of the booking confirmation letter. The booking engine (a private Google Apps Script,
not stored here) copies it into Google Drive once as a Google Doc, then fills the `{{fields}}` for each booking and removes
paragraphs marked `[[if deposit]]`, `[[if full]]` or `[[if dogs]]` that don't apply.

It holds no guest details, prices or bank details: those are filled in privately for each booking.
Rebuild it with `python3 build_template.py waves-small.png letter-template.docx` (needs python-docx).
