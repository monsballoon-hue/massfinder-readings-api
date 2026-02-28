from http.server import BaseHTTPRequestHandler
import asyncio
import datetime
import json
from urllib.parse import urlparse, parse_qs

from catholic_mass_readings import USCCB, models


def _safe_ref(reading):
    """Get a scripture reference string from a Reading's verses."""
    if not reading.verses:
        return ""
    return ", ".join(v.text for v in reading.verses if v.text)


async def _get_readings(date: datetime.date):
    """Fetch readings for a date, trying standard mass types in order."""
    async with USCCB() as usccb:
        mass = await usccb.get_mass_from_date(
            date,
            types=[
                models.MassType.DEFAULT,
                models.MassType.DAY,
                models.MassType.YEARA,
                models.MassType.YEARB,
                models.MassType.YEARC,
            ],
        )
    return mass


class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        try:
            # Parse ?date=YYYYMMDD from query string
            qs = parse_qs(urlparse(self.path).query)
            date_str = qs.get("date", [None])[0]

            if not date_str:
                self._respond(400, {"error": "missing date parameter"})
                return

            try:
                date = datetime.datetime.strptime(date_str, "%Y%m%d").date()
            except ValueError:
                self._respond(400, {"error": f"invalid date format: {date_str}, expected YYYYMMDD"})
                return

            mass = asyncio.run(_get_readings(date))

            if not mass:
                self._respond(404, {"error": f"no readings found for {date.isoformat()}"})
                return

            # Build a compact response — just what MassFinder needs:
            # title (liturgical day name) + section headings + scripture refs
            sections = []
            for section in mass.sections:
                # Skip Alleluia/Sequence — not useful to display as a reading row
                if section.type_ in (
                    models.SectionType.ALLELUIA,
                    models.SectionType.SEQUENCE,
                ):
                    continue

                # Use the first (canonical) reading's reference
                if section.readings:
                    ref = _safe_ref(section.readings[0])
                else:
                    ref = ""

                sections.append({
                    "heading": section.display_header,
                    "ref": ref,
                })

            payload = {
                "date": date.isoformat(),
                "title": mass.title,
                "url": mass.url,
                "sections": sections,
            }

            self._respond(200, payload)

        except Exception as e:
            self._respond(500, {"error": str(e)})

    def _respond(self, status: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # suppress default access logs
