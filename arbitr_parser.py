import aiohttp
import asyncio
import logging

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

KAD_SEARCH_URL = "https://kad.arbitr.ru/Kad/SearchInstances"


class ArbitrParser:

    async def get_company_name(self, inn: str) -> str | None:
        """Получает название компании по ИНН через ЕГРЮЛ"""
        try:
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                async with session.get(
                    "https://egrul.nalog.ru/",
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    pass

                async with session.post(
                    "https://egrul.nalog.ru/search-show",
                    data={"query": inn, "region": "", "page": ""},
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status != 200:
                        return inn
                    data = await resp.json()
                    items = data.get("rows", [])
                    if not items:
                        return inn
                    name = items[0].get("n") or items[0].get("c") or inn
                    return name
        except Exception as e:
            logger.error(f"Ошибка получения названия компании {inn}: {e}")
            return inn

    async def get_cases(self, inn: str) -> list:
        """Получает список дел из kad.arbitr.ru по ИНН."""
        try:
            payload = {
                "Page": 1,
                "Count": 100,
                "DateFrom": None,
                "DateTo": None,
                "Sides": [{"Name": "", "Inn": inn, "Type": "MixedParticipant"}],
                "Judges": [],
                "CaseNumbers": [],
                "Courts": [],
                "Cases": [],
                "CourtType": "Arbitrage",
                "SearchByCommonPleas": False,
                "SearchByBankruptcyCases": False,
            }

            async with aiohttp.ClientSession(headers=HEADERS) as session:
                async with session.post(
                    KAD_SEARCH_URL,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"kad.arbitr.ru вернул {resp.status} для {inn}")
                        return []
                    data = await resp.json()

            items = data.get("Result", {}).get("Items", [])
            cases = []
            for item in items:
                case = self._parse_case_item(item)
                if case:
                    cases.append(case)
            return cases

        except asyncio.TimeoutError:
            logger.error(f"Таймаут при запросе дел для {inn}")
            return []
        except Exception as e:
            logger.error(f"Ошибка получения дел для {inn}: {e}")
            return []

    def _parse_case_item(self, item: dict) -> dict | None:
        try:
            case_id = item.get("CaseId") or item.get("Id") or ""
            number = item.get("CaseNumber") or item.get("Number") or ""
            court = item.get("CourtName") or ""
            status = item.get("StateName") or item.get("Status") or ""
            judge = item.get("JudgeName") or ""

            amount_raw = item.get("ClaimSum") or item.get("Sum") or 0
            amount = f"{amount_raw:,.2f} ₽" if amount_raw else ""

            plaintiff = ""
            defendant = ""
            sides = item.get("Sides") or []
            for side in sides:
                side_type = (side.get("Type") or "").lower()
                side_name = side.get("Name") or ""
                if "истец" in side_type or "plaintiff" in side_type:
                    plaintiff = side_name
                elif "ответчик" in side_type or "defendant" in side_type:
                    defendant = side_name

            next_hearing = item.get("NextHearingDate") or item.get("HearingDate") or ""
            if next_hearing and "T" in next_hearing:
                next_hearing = next_hearing.split("T")[0]

            documents = []
            docs_raw = item.get("Documents") or []
            for doc in docs_raw:
                doc_name = doc.get("Name") or doc.get("Type") or ""
                if doc_name:
                    documents.append(doc_name)

            return {
                "case_id": str(case_id),
                "number": number,
                "court": court,
                "status": status,
                "judge": judge,
                "amount": amount,
                "plaintiff": plaintiff,
                "defendant": defendant,
                "next_hearing": next_hearing,
                "documents": documents,
            }
        except Exception as e:
            logger.error(f"Ошибка парсинга дела: {e}")
            return None
