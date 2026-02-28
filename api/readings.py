from http.server import BaseHTTPRequestHandler
import asyncio
import datetime
import json
from urllib.parse import urlparse, parse_qs

from catholic_mass_readings import USCCB, models


def _safe_ref(reading):
    if not reading.verses:
        return ""
    return ", ".join(v.text for v in reading.verses if v.text)


async def _get_readings(date: datetime.date):
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
            qs = parse_qs(urlparse(self.path).query)
            date_str = qs.get("date", [None])[0]

            if not date_str:
                self._respond(400, {"error": "missing date parameter"})
                return

            try:
                date = datetime.datetime.strptime(date_str, "%Y%m%d").date()
            except ValueError:
                self._respond(400, {"error": f"invalid date: {date_str}"})
                return

            mass = asyncio.run(_get_readings(date))

            if not mass:
                self._respond(404, {"error": f"no readings found for {date.isoformat()}"})
                return

            sections = []
            for section in mass.sections:
                if section.type_ in (
                    models.SectionType.ALLELUIA,
                    models.SectionType.SEQUENCE,
                ):
                    continue

                if not section.readings:
                    continue

                # Primary reading (first); include ref + full text
                primary = section.readings[0]
                sections.append({
                    "heading": section.display_header,
                    "ref": _safe_ref(primary),
                    "text": primary.text.strip() if primary.text else "",
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
        pass
