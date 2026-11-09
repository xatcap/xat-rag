"""Write the PDF, DOCX, HTML and Excel files of the evaluation corpus, and their golden cases.

All formats hold the same fictional handbook, so a format can be compared with another. Each fact has one
question and one answer text. A case is relevant by answer text, because chunk IDs change when the split rules change.

    uv run python tools/make_eval_documents.py
"""

import html
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import docx
import openpyxl
from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import BaseDocTemplate, Flowable, Frame, PageTemplate, Paragraph, Table, TableStyle

rl_config.invariant = 1

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "eval" / "fixtures"
DOCUMENTS = FIXTURES / "setup" / "documents"
GOLDEN = FIXTURES / "golden.jsonl"
FORMATS = {
    "pdf": ("pdfs", "ask-pdfs", ".pdf"),
    "docx": ("word", "ask-word", ".docx"),
    "html": ("web", "ask-web", ".html"),
    "xlsx": ("sheets", "ask-sheets", ".xlsx"),
}


@dataclass(frozen=True)
class Fact:
    """One paragraph with a fact, and its question and answer text."""

    text: str
    question: str
    answer: str


@dataclass(frozen=True)
class FactTable:
    """A table. Each entry of `facts` is a question and the answer text, in the order of the row cells."""

    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    facts: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Section:
    """A section with a heading of level 2. It holds several topics, with no heading between them."""

    title: str
    body: tuple[Fact | FactTable, ...]


@dataclass(frozen=True)
class Part:
    """One file of the corpus."""

    name: str
    title: str
    columns: int
    sections: tuple[Section, ...] = field(default=())


PARTS = (
    Part(
        "warehouse",
        "Halden Warehouse Operations",
        1,
        (
            Section(
                "Receiving goods",
                (
                    Fact(
                        "Every inbound pallet is scanned at dock door before the driver signs the delivery note. "
                        "The receiving clerk compares the count with the advance shipping notice and records any "
                        "difference in the discrepancy log within 45 minutes of unloading. Damaged cartons stay on "
                        "the quarantine rack until a supervisor has inspected them and released them for putaway.",
                        "How quickly must a count difference be written down after a truck is unloaded?",
                        "within 45 minutes of unloading",
                    ),
                ),
            ),
            Section(
                "Cold storage",
                (
                    Fact(
                        "Frozen goods are held in chamber four, where the setpoint is minus 23 degrees Celsius. The "
                        "building management system raises an alarm when a door stays open for longer than ninety "
                        "seconds, and the shift lead must then walk the chamber and sign the temperature sheet.",
                        "What temperature is the frozen goods chamber kept at?",
                        "minus 23 degrees Celsius",
                    ),
                ),
            ),
            Section(
                "Equipment and site safety",
                (
                    Fact(
                        "Forklift drivers renew their operating licence every 18 months at the Tromsdal training "
                        "centre. The course includes a written paper and a practical assessment in a marked yard. A "
                        "driver whose licence has lapsed is moved to manual picking until the renewal is complete, "
                        "and the transport office removes the driver badge from the truck access system.",
                        "How often does a forklift licence have to be renewed?",
                        "every 18 months",
                    ),
                    Fact(
                        "Racking inspections are the responsibility of the facilities engineer, who walks every aisle "
                        "on the first Tuesday of each quarter. Bent uprights are tagged in red and the bay is unloaded "
                        "the same day. Repairs use only parts from the original manufacturer, because mixed components "
                        "void the load rating printed on the end of each aisle.",
                        "On which day are the storage racks inspected?",
                        "first Tuesday of each quarter",
                    ),
                    Fact(
                        "Hearing protection is compulsory in the conveyor hall, where sound levels measured "
                        "last spring "
                        "reached 88 decibels near the sorter. Visitors collect disposable ear plugs from the dispenser "
                        "at the hall entrance. Employees with moulded plugs have them tested once a year by the "
                        "occupational health nurse who visits the site.",
                        "What noise level was measured close to the sorter?",
                        "88 decibels",
                    ),
                    Fact(
                        "Fire doors between the mezzanine and the packing area close automatically when the alarm "
                        "panel triggers. Staff assemble at muster point Kestrel, on the far side of the employee car "
                        "park, and team leaders count their people against the electronic roll call on the shared "
                        "tablet. Nobody goes back inside until the fire warden has given the all clear.",
                        "Where do staff gather after a fire alarm?",
                        "muster point Kestrel",
                    ),
                    Fact(
                        "Battery charging takes place in a ventilated bay behind gate seven. Lead acid batteries need "
                        "a cooling period of six hours after a full charge before they go back in a truck, and the "
                        "charging log records the serial number of each unit. Spilled electrolyte is neutralised with "
                        "the soda ash kept in the yellow bin beside the eyewash station.",
                        "How long must a lead acid battery cool after charging?",
                        "cooling period of six hours",
                    ),
                ),
            ),
            Section(
                "Picking and dispatch",
                (
                    Fact(
                        "Pickers follow the route shown on the wrist scanner, which groups orders into waves of up to "
                        "forty lines. Fragile lines are always picked last, so that nothing heavy is placed on top of "
                        "them in the tote. A picker who cannot find an item reports a short pick and the scanner sends "
                        "the task to the inventory controller.",
                        "How many lines can one picking wave contain?",
                        "waves of up to forty lines",
                    ),
                    Fact(
                        "The final dispatch cut-off for next day delivery is 17:40 on working days. Parcels that miss "
                        "this time are held on the overflow lane and leave on the early morning trunk vehicle. During "
                        "the peak season the transport planner may publish a later time on the dispatch notice board.",
                        "What is the last dispatch time for next day delivery?",
                        "cut-off for next day delivery is 17:40",
                    ),
                    Fact(
                        "Returned parcels arrive at bay twelve and are graded by the returns team. Grade C stock is "
                        "sold to the liquidation partner Morvik Traders at the end of each month, while grade A stock "
                        "goes straight back into its original location. Items with a broken seal are never returned "
                        "to saleable stock, whatever their outward condition.",
                        "Which company buys grade C returned stock?",
                        "Morvik Traders",
                    ),
                ),
            ),
        ),
    ),
    Part(
        "people",
        "Halden People and Travel Guide",
        2,
        (
            Section(
                "Leave and working time",
                (
                    Fact(
                        "New colleagues receive 27 days of paid annual leave in their first full calendar year. Leave "
                        "requests go through the rota application and need the approval of the line manager at least "
                        "two weeks ahead. Unused days do not lapse straight away, because the carry over rule allows "
                        "a balance to move into the first quarter of the next year.",
                        "How many days of annual leave does a new colleague get?",
                        "27 days of paid annual leave",
                    ),
                    Fact(
                        "Parents may take an additional sixteen weeks of shared parental leave, which can be divided "
                        "into up to three separate blocks. The people team needs written notice eight weeks before the "
                        "first block starts. Pay during this period follows the company scheme described in the "
                        "benefits booklet given to every employee on joining.",
                        "How much shared parental leave can parents take?",
                        "sixteen weeks of shared parental leave",
                    ),
                    Fact(
                        "Overtime is paid at one and a half times the basic rate for weekday shifts and double time on "
                        "public holidays. Time recorded in the attendance system is rounded to the nearest quarter of "
                        "an hour. Supervisors confirm overtime on the Friday of each week so that it can go into the "
                        "following payroll run without manual correction.",
                        "What is the overtime rate on public holidays?",
                        "double time on public holidays",
                    ),
                ),
            ),
            Section(
                "Travel and expenses",
                (
                    Fact(
                        "Rail journeys longer than three hours may be booked in first class, and every other trip is "
                        "standard class. Tickets are bought through the Voyara portal so that the travel team can "
                        "contact staff during a disruption. Taxi receipts must show the date, the fare and the name of "
                        "the operator before finance will accept them.",
                        "Which portal is used to buy rail tickets?",
                        "Voyara portal",
                    ),
                    Fact(
                        "Meals on business trips are reimbursed up to a daily limit of 62 euros, including tips and "
                        "drinks taken with food. Alcohol bought separately is never reimbursed. Claims are submitted "
                        "with photographs of the receipts, and a claim older than ninety days needs a signature from "
                        "the finance director before it can be paid.",
                        "What is the daily meal allowance when travelling?",
                        "daily limit of 62 euros",
                    ),
                    Fact(
                        "Staff who drive their own car on company business claim a mileage rate of 41 cents for each "
                        "kilometre. The driver must hold business insurance cover and upload the certificate once a "
                        "year. Fines for parking and speeding remain the personal responsibility of the driver in "
                        "every case and cannot be added to an expense claim.",
                        "How much is paid per kilometre for private car use?",
                        "41 cents for each kilometre",
                    ),
                ),
            ),
            Section(
                "Learning and wellbeing",
                (
                    Fact(
                        "Each employee has a personal learning budget of 900 euros per year for courses, books and "
                        "conference tickets. The budget is requested through the learning catalogue and a manager "
                        "approves the purpose rather than the provider. Any amount that remains unspent returns to the "
                        "central fund when the financial year closes.",
                        "How large is the yearly personal learning budget?",
                        "learning budget of 900 euros",
                    ),
                    Fact(
                        "Confidential counselling is available through the Brightharbor helpline at any hour of the "
                        "day. Six free sessions are offered per issue, and neither the company nor the line manager "
                        "is told who has called. Trained mental health first aiders are also present at each site "
                        "and their names are listed in the canteen.",
                        "Which service provides confidential counselling?",
                        "Brightharbor helpline",
                    ),
                    Fact(
                        "The annual health check takes place in the second half of October in a mobile clinic parked "
                        "beside the main entrance. It covers blood pressure, eyesight and a short lifestyle "
                        "questionnaire. Results go only to the employee, and the nurse can refer anyone who needs "
                        "further attention to their own doctor.",
                        "When does the annual health check happen?",
                        "second half of October",
                    ),
                    Fact(
                        "Staff who cycle to work can use the secure shelter behind building two, which is opened with "
                        "the same badge as the front door. The tax scheme for buying a bicycle allows a maximum "
                        "purchase of 2400 euros, repaid from gross salary across three years. Showers and lockers are "
                        "on the ground floor next to the gym.",
                        "What is the maximum bicycle purchase under the tax scheme?",
                        "maximum purchase of 2400 euros",
                    ),
                ),
            ),
        ),
    ),
    Part(
        "carriers",
        "Halden Carrier and Claims Manual",
        1,
        (
            Section(
                "Carrier contracts",
                (
                    FactTable(
                        ("Carrier", "Claim window in days", "Main region"),
                        (
                            ("Zephyr Freight", "14", "Nordics"),
                            ("Bluecrest Haulage", "21", "Benelux"),
                            ("Ostrava Linehaul", "30", "Central Europe"),
                            ("Marisol Express", "10", "Iberia"),
                        ),
                        (
                            ("How many days is the claim window for Zephyr Freight?", "Zephyr Freight 14"),
                            ("What is the claim window for Bluecrest Haulage?", "Bluecrest Haulage 21"),
                            ("Which region does Ostrava Linehaul mainly cover?", "Ostrava Linehaul 30 Central Europe"),
                            ("How long is the Marisol Express claim window?", "Marisol Express 10"),
                        ),
                    ),
                ),
            ),
            Section(
                "Claims handling",
                (
                    Fact(
                        "A claim for lost goods opens when the tracking record has shown no movement for nine working "
                        "days. The claims officer attaches the commercial invoice and the signed proof of collection, "
                        "then sends the file to the carrier through its online claims desk. Each file receives a "
                        "reference which starts with the letters HCL.",
                        "After how long without tracking movement can a lost goods claim be opened?",
                        "no movement for nine working days",
                    ),
                    Fact(
                        "Compensation for road transport is limited by convention to 8.33 special drawing rights per "
                        "kilogram of gross weight. Customers who need more cover may buy the declared value option "
                        "when they book. Consequential loss such as a missed sale is excluded from the standard terms "
                        "and from most of the optional cover.",
                        "What is the compensation limit per kilogram for road transport?",
                        "8.33 special drawing rights per kilogram",
                    ),
                    Fact(
                        "Claims above 5000 euros go to the regional claims panel, which meets on alternate Thursdays. "
                        "The panel reviews photographs, driver statements and any survey report before it agrees a "
                        "settlement. Its decision is recorded in the claims register and copied to the account manager "
                        "of the customer concerned.",
                        "Which claims have to be reviewed by the regional panel?",
                        "Claims above 5000 euros",
                    ),
                ),
            ),
            Section(
                "Hazardous goods",
                (
                    Fact(
                        "Lithium batteries shipped by road travel under UN 3480 unless they are packed with equipment. "
                        "Packages carry the battery mark and a telephone number for further information. The dangerous "
                        "goods adviser audits a sample of shipments and reports any faults to the operations director "
                        "in an annual written report.",
                        "Which UN number applies to lithium batteries shipped alone?",
                        "UN 3480",
                    ),
                    Fact(
                        "Drivers carrying dangerous goods above the exempt threshold must hold a valid ADR certificate "
                        "and must complete a refresher every five years. Vehicles are fitted with orange plates "
                        "and two "
                        "fire extinguishers, and the transport document stays in the cab within reach of the driver "
                        "for the entire journey.",
                        "How frequently must drivers refresh their ADR training?",
                        "refresher every five years",
                    ),
                    Fact(
                        "Aerosols are stored in the caged area at the east wall, separated from oxidising "
                        "substances by "
                        "at least three metres. Stock in the cage is capped at 600 kilograms of net contents in order "
                        "to stay within the conditions of the site permit. Damaged cans go into the punctured aerosol "
                        "drum and are removed by a licensed contractor.",
                        "What is the maximum quantity of aerosols in the cage?",
                        "600 kilograms of net contents",
                    ),
                ),
            ),
        ),
    ),
)


def _write_pdf(part: Part, path: Path) -> None:
    styles = getSampleStyleSheet()
    width, height = A4

    def decorate(canvas: Canvas, document: BaseDocTemplate) -> None:
        canvas.setFont("Helvetica", 8)
        canvas.drawString(50, height - 30, part.title)
        canvas.drawString(50, 25, f"Internal use only. Page {document.page}")  # pyright: ignore[reportUnknownMemberType]

    if part.columns == 2:
        frames = [Frame(40, 45, width / 2 - 50, height - 90), Frame(width / 2 + 10, 45, width / 2 - 50, height - 90)]
    else:
        frames = [Frame(50, 45, width - 100, height - 90)]
    # Large text makes the paragraphs cross page ends.
    body = ParagraphStyle("LargeBody", parent=styles["BodyText"], fontSize=15, leading=27)
    document = BaseDocTemplate(str(path), pagesize=A4, title=part.title, author="", invariant=1)
    document.addPageTemplates([PageTemplate(id="body", frames=frames, onPage=decorate)])
    story: list[Flowable] = [Paragraph(part.title, styles["Heading1"])]
    for section in part.sections:
        story.append(Paragraph(section.title, styles["Heading2"]))
        for item in section.body:
            if isinstance(item, Fact):
                story.append(Paragraph(html.escape(item.text), body))
            else:
                style = TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)])
                story.append(Table([list(item.columns), *[list(row) for row in item.rows]], style=style))
    document.build(story)


def _write_docx(part: Part, path: Path) -> None:
    document = docx.Document()
    document.core_properties.title = part.title
    document.add_heading(part.title, 1)
    for section in part.sections:
        document.add_heading(section.title, 2)
        for item in section.body:
            if isinstance(item, Fact):
                document.add_paragraph(item.text)
            else:
                table = document.add_table(len(item.rows) + 1, len(item.columns))
                for row_index, row in enumerate((item.columns, *item.rows)):
                    for column, value in enumerate(row):
                        table.cell(row_index, column).text = value
    document.save(str(path))


def _write_html(part: Part, path: Path) -> None:
    lines = [
        "<!doctype html>",
        f"<html><head><title>{html.escape(part.title)}</title></head><body>",
        '<nav><a href="/">Home</a> <a href="/handbooks">Handbooks</a></nav>',
        f"<article><h1>{html.escape(part.title)}</h1>",
    ]
    for section in part.sections:
        lines.append(f"<h2>{html.escape(section.title)}</h2>")
        for item in section.body:
            if isinstance(item, Fact):
                lines.append(f"<p>{html.escape(item.text)}</p>")
            else:
                head = "".join(f"<th>{html.escape(value)}</th>" for value in item.columns)
                rows = "".join(
                    "<tr>" + "".join(f"<td>{html.escape(v)}</td>" for v in row) + "</tr>" for row in item.rows
                )
                lines.append(f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>")
    lines += ["</article><footer>Halden internal publication</footer>", "</body></html>"]
    path.write_text("\n".join(lines) + "\n", "utf-8")


def _write_xlsx(part: Part, path: Path) -> None:
    book = openpyxl.Workbook()
    book.remove(book.active)  # pyright: ignore[reportArgumentType]
    for section in part.sections:
        sheet = book.create_sheet(section.title[:31])
        first = section.body[0]
        if isinstance(first, FactTable):
            for row in (first.columns, *first.rows):
                sheet.append(list(row))
            continue
        sheet.append(["Topic", "Detail"])
        for number, item in enumerate(section.body, start=1):
            sheet.append([number, item.text if isinstance(item, Fact) else ""])
    book.save(str(path))


def _facts(part: Part) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for section in part.sections:
        for item in section.body:
            found += [(item.question, item.answer)] if isinstance(item, Fact) else list(item.facts)
    return found


def _golden_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for kind, (rag, context, _suffix) in FORMATS.items():
        number = 0
        for part in PARTS:
            for question, answer in _facts(part):
                number += 1
                relevant = [{"text": answer, "grade": 2}]
                cases.append(
                    {
                        "id": f"{kind}-{number:02d}",
                        "context": context,
                        "rag": rag,
                        "kind": kind,
                        "question": question,
                        "relevant": relevant,
                    }
                )
    return cases


def main() -> None:
    """Write the documents, and replace the generated cases of the golden set."""
    writers = {"pdf": _write_pdf, "docx": _write_docx, "html": _write_html, "xlsx": _write_xlsx}
    for kind, (rag, _context, suffix) in FORMATS.items():
        folder = DOCUMENTS / rag
        folder.mkdir(parents=True, exist_ok=True)
        for part in PARTS:
            writers[kind](part, folder / f"{part.name}{suffix}")
    generated = tuple(f"{kind}-" for kind in FORMATS)
    kept = [
        line
        for line in GOLDEN.read_text("utf-8").splitlines()
        if line.strip() and not str(json.loads(line)["id"]).startswith(generated)
    ]
    GOLDEN.write_text("\n".join([*kept, *(json.dumps(case) for case in _golden_cases())]) + "\n", "utf-8")
    print(f"wrote {len(PARTS) * len(FORMATS)} documents and {len(_golden_cases())} golden cases")


if __name__ == "__main__":
    main()
