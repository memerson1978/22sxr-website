#!/usr/bin/env python3
"""Builds the booking confirmation letter template as a Word file, uploaded to Google Drive as a Google Doc.
The booking engine copies the Doc for each booking, fills the {{fields}}, removes paragraphs marked [[if X]] that
don't apply (deposit, full, dogs), and saves a PDF. Wording matches the approved letter (5th November 2026)."""
from docx import Document
from docx.shared import Mm, Pt, RGBColor
from docx.enum.text import WD_TAB_ALIGNMENT, WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
import sys

INK, INK2, TIDE, FOG, LINE = "1B2F40", "4A5B69", "356B8F", "EDF1F4", "D5DDE3"
BODY, DISPLAY = "Hanken Grotesk", "Marcellus"

doc = Document()
sec = doc.sections[0]
sec.page_width, sec.page_height = Mm(210), Mm(297)
sec.top_margin, sec.bottom_margin, sec.left_margin, sec.right_margin = Mm(11), Mm(14), Mm(16), Mm(16)
sec.footer_distance = Mm(7)
W = 178  # content width mm

st = doc.styles["Normal"]
st.font.name, st.font.size, st.font.color.rgb = BODY, Pt(9), RGBColor.from_string(INK)
st.element.rPr.rFonts.set(qn("w:eastAsia"), BODY)
st.paragraph_format.space_before, st.paragraph_format.space_after, st.paragraph_format.line_spacing = Pt(0), Pt(0), 1.18


def run(p, text, size=None, bold=False, color=None, font=None):
    r = p.add_run(text)
    if size: r.font.size = Pt(size)
    if bold: r.bold = True
    if color: r.font.color.rgb = RGBColor.from_string(color)
    if font:
        r.font.name = font
        r.element.rPr.rFonts.set(qn("w:hAnsi"), font); r.element.rPr.rFonts.set(qn("w:ascii"), font)
    return r


def para(container, text="", size=None, bold=False, color=None, font=None, after=0, before=0, align=None):
    p = container.add_paragraph()
    pf = p.paragraph_format
    pf.space_after, pf.space_before = Pt(after), Pt(before)
    if align: p.alignment = align
    if text: run(p, text, size, bold, color, font)
    return p


def border(p, side, sz_eighths, color, space=1):
    pPr = p._p.get_or_add_pPr()
    b = pPr.find(qn("w:pBdr"))
    if b is None:
        b = OxmlElement("w:pBdr"); pPr.append(b)
    e = OxmlElement(f"w:{side}")
    e.set(qn("w:val"), "single"); e.set(qn("w:sz"), str(sz_eighths)); e.set(qn("w:space"), str(space)); e.set(qn("w:color"), color)
    b.append(e)


def shade(p, fill):
    pPr = p._p.get_or_add_pPr()
    s = OxmlElement("w:shd"); s.set(qn("w:val"), "clear"); s.set(qn("w:color"), "auto"); s.set(qn("w:fill"), fill)
    pPr.append(s)


def layout_table(cols_mm):
    t = doc.add_table(rows=1, cols=len(cols_mm))
    t.alignment = WD_TABLE_ALIGNMENT.LEFT
    t.autofit = False
    tblPr = t._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        e = OxmlElement(f"w:{side}"); e.set(qn("w:val"), "nil"); borders.append(e)
    tblPr.append(borders)
    mar = OxmlElement("w:tblCellMar")
    for side in ("left", "right", "top", "bottom"):
        e = OxmlElement(f"w:{side}"); e.set(qn("w:w"), "0"); e.set(qn("w:type"), "dxa"); mar.append(e)
    tblPr.append(mar)
    for i, w in enumerate(cols_mm):
        t.columns[i].width = Mm(w)
        t.rows[0].cells[i].width = Mm(w)
    for c in t.rows[0].cells:  # remove the default empty paragraph later via first-use
        c._tc.remove(c.paragraphs[0]._p)
    return t.rows[0].cells


def row_line(cell, label, value, tab_mm, width_mm, right=False, bold=False, top=False):
    """label<TAB>value with a hairline under it. Left labels use a hanging indent so long values wrap neatly."""
    p = para(cell)
    pf = p.paragraph_format
    pf.space_before, pf.space_after = Pt(1.6), Pt(1.6)
    if right:
        pf.tab_stops.add_tab_stop(Mm(width_mm), WD_TAB_ALIGNMENT.RIGHT)
    else:
        pf.tab_stops.add_tab_stop(Mm(tab_mm))
        pf.left_indent, pf.first_line_indent = Mm(tab_mm), Mm(-tab_mm)
    run(p, label, color=None if bold else INK2, bold=bold)
    run(p, "\t" + value, bold=bold)
    if top: border(p, "top", 8, INK, 2)
    else: border(p, "bottom", 4, LINE, 2)
    return p


# ---------------------------------------------------------------- letterhead
left, right = layout_table([92, 86])
p = para(left)
p.add_run().add_picture(sys.argv[1] if len(sys.argv) > 1 else "waves.png", width=Mm(14))
run(p, "   22 Saxonbury Road", 19, font=DISPLAY)
para(left, "Southbourne, Dorset, BH6 5NB", 8.6, color=INK2, before=1).paragraph_format.left_indent = Mm(21.2)
for i, (t, b) in enumerate([("Bournepad Ltd", True), ("Foxlea, The Mount, Barley, Royston, Hertfordshire SG8 8JH", False),
                            ("Mark: 07932 712565   {{second_contact}}", False), ("stay@22sxr.co.uk   www.22sxr.co.uk", False)]):
    para(right, t, 8, bold=b, color=INK if b else INK2, align=WD_ALIGN_PARAGRAPH.RIGHT, after=1)
rule = para(doc, "", 2, after=6)
border(rule, "bottom", 10, TIDE, 4)

# ---------------------------------------------------------------- addressee and date
left, right = layout_table([120, 58])
para(left, "{{guest_name}}", 8.8)
para(left, "{{address}}", 8.8)
para(right, "{{letter_date}}", 8.8, align=WD_ALIGN_PARAGRAPH.RIGHT)

para(doc, "Booking confirmation", 18, font=DISPLAY, before=10)
para(doc, "Reference {{reference}}", 9.5, bold=True, color=TIDE, before=3)
para(doc, "Dear {{first_name}},", before=9, after=4)
para(doc, "Thank you for booking 22 Saxonbury Road. Your dates are reserved for you, and this letter confirms the details of your stay.", after=7)

s = para(doc, after=9)
s.paragraph_format.left_indent, s.paragraph_format.right_indent = Mm(3), Mm(0)
run(s, "Your booking is confirmed subject to receiving {{first_word}} of {{first_amount}} by {{first_due}}.", bold=True)
run(s, " Once it arrives, your booking is certain. If it hasn't arrived by then, we'll send you a polite reminder, and if it still hasn't arrived within 24 hours, we may release the dates.")
shade(s, FOG)
border(s, "left", 24, TIDE, 6)

# ---------------------------------------------------------------- your stay | price
L, R = 95, 75
left, gap, right = layout_table([L, 8, R])
para(left, "Your stay", 11.5, color=TIDE, font=DISPLAY, after=2)
for k, v in [("Arrival", "{{arrival_long}}, anytime from 3pm"), ("Departure", "{{departure_long}}, by 10am"),
             ("Nights", "{{nights}}"), ("Guests", "{{guests}}"), ("Dogs", "{{dogs_count}}")]:
    row_line(left, k, v, 23, L)
para(gap, "")
para(right, "Price", 11.5, color=TIDE, font=DISPLAY, after=2)
row_line(right, "Accommodation, {{nights}} nights", "{{acc}}", 0, R, right=True)
row_line(right, "Cleaning and linen", "{{clean}}", 0, R, right=True)
row_line(right, "[[if dogs]]{{dog_label}}", "{{dog_fee}}", 0, R, right=True)
row_line(right, "Total", "{{total}}", 0, R, right=True, bold=True, top=True)

# ---------------------------------------------------------------- payment | cancel
para(doc, "", 4, after=4)
left, gap, right = layout_table([L, 8, R])
para(left, "Payment", 11.5, color=TIDE, font=DISPLAY, after=2)
for label, amount, due in [("{{first_label}}", "{{first_amount}}", "by {{first_due}}"),
                           ("[[if deposit]]Balance", "{{balance_amount}}", "by {{balance_due}}")]:
    p = para(left)
    pf = p.paragraph_format
    pf.space_before, pf.space_after = Pt(1.6), Pt(1.6)
    pf.tab_stops.add_tab_stop(Mm(46), WD_TAB_ALIGNMENT.RIGHT)
    pf.tab_stops.add_tab_stop(Mm(50))
    run(p, label); run(p, "\t" + amount); run(p, "\t" + due)
    border(p, "bottom", 4, LINE, 2)
para(left, "Please pay by bank transfer:", before=6, after=2)
for k, v in [("Account name", "Bournepad Limited"), ("Sort code", "{{sort_code}}"), ("Account number", "{{account_number}}")]:
    p = para(left, after=1)
    p.paragraph_format.tab_stops.add_tab_stop(Mm(34))
    run(p, k, color=INK2); run(p, "\t" + v)
p = para(left, after=1)
p.paragraph_format.tab_stops.add_tab_stop(Mm(34))
run(p, "Reference", color=INK2); run(p, "\t{{reference}}", bold=True)
para(gap, "")
para(right, "If you need to cancel", 11.5, color=TIDE, font=DISPLAY, after=2)
para(right, "Please tell us in writing. Four months or more before arrival, we refund everything you have paid, less a £25 admin fee. "
            "From four months to six weeks before arrival, the deposit is kept. Within six weeks of arrival, all payments are kept. "
            "If we manage to relet the dates, we may make a partial refund at our discretion, as set out in our terms.", after=5)
para(right, "We recommend taking out travel insurance now that you have booked.", bold=True)

# ---------------------------------------------------------------- next steps
para(doc, "What happens next", 11.5, color=TIDE, font=DISPLAY, before=9, after=3)
for t in ["Pay {{first_pay_words}} of {{first_amount}} by {{first_due}}. We'll let you know when it arrives.",
          "[[if deposit]]We'll send a polite reminder a week before your balance is due on {{balance_due}}.",
          "Three days before you arrive, we'll email your welcome guide and how to access your key."]:
    p = doc.add_paragraph(style="List Number")
    p.paragraph_format.space_after = Pt(2.5)
    p.paragraph_format.left_indent, p.paragraph_format.first_line_indent = Mm(5), Mm(-5)
    run(p, t)

p = para(doc, before=6)
run(p, "[[if dogs]]Dogs.", bold=True)
run(p, " Your dog is very welcome. Please keep dogs off the furniture and beds, ideally downstairs, and don't leave them alone in the house.")

para(doc, "Your booking is subject to our terms and conditions, which you agreed to when you booked: www.22sxr.co.uk/terms. "
          "If anything changes, just let us know. We look forward to welcoming you.", before=8)
para(doc, "Best wishes,", before=8)
para(doc, "Mark Emerson", bold=True, before=8)
para(doc, "Bournepad Ltd")

# ---------------------------------------------------------------- footer
f = sec.footer
fp = f.paragraphs[0]
run(fp, "Bournepad Ltd, registered in England and Wales, company number 13055058. Registered office: Foxlea, The Mount, Barley, Royston, Hertfordshire SG8 8JH.", 7.6, color=INK2)
border(fp, "top", 4, LINE, 4)

# number list style font
ln = doc.styles["List Number"]
ln.font.name, ln.font.size = BODY, Pt(9)

out = sys.argv[2] if len(sys.argv) > 2 else "22 SXR booking confirmation template.docx"
doc.save(out)

# Slim the file (the default Word template carries large unused parts): drop stylesWithEffects and the thumbnail.
import zipfile, shutil, re, os
tmp = out + ".tmp"
with zipfile.ZipFile(out) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zout:
    for item in zin.infolist():
        if item.filename in ("word/stylesWithEffects.xml", "docProps/thumbnail.jpeg"):
            continue
        data = zin.read(item.filename)
        if item.filename in ("[Content_Types].xml", "word/_rels/document.xml.rels", "_rels/.rels"):
            t = data.decode()
            t = re.sub(r'<Override[^>]*stylesWithEffects[^>]*/>', '', t)
            t = re.sub(r'<Override[^>]*thumbnail[^>]*/>', '', t)
            t = re.sub(r'<Relationship[^>]*stylesWithEffects[^>]*/>', '', t)
            t = re.sub(r'<Relationship[^>]*thumbnail[^>]*/>', '', t)
            data = t.encode()
        zout.writestr(item, data)
shutil.move(tmp, out)
print("saved", out, os.path.getsize(out), "bytes")
