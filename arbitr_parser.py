import aiohttp
import asyncio
import logging

logger = logging.getLogger(__name__)

# Официальный API kad.arbitr.ru
KAD_API = "https://kad.arbitr.ru/Kad/SearchInstances"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://kad.arbitr.ru",
    "Referer": "https://kad.arbitr.ru/",
    "X-Requested-With": "XMLHttpRequest",
}


class ArbitrParser:

    async def get_company_name(self, inn: str) -> str | None:
        """Получает название компании через ЕГРЮЛ nalog.ru"""
        try:
            async with aiohttp.ClientSession() as session:
                # Сначала заходим на сайт для получения кук
                await session.get("https://egrul.nalog.ru/", timeout=aiohttp.ClientTimeout(total=10))

                async with session.post(
                    "https://egrul.nalog.ru/search-show",
                    data={"query": inn, "region": "", "page": ""},
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status != 200:
                        return inn
                    data = await resp.json()
                    rows = data.get("rows", [])
                    if not rows:
                        return inn
                    return rows[0].get("n") or rows[0].get("c") or inn
        except Exception as e:
            logger.error(f"Ошибка ЕГРЮЛ для {inn}: {e}")
            return inn

    async def get_cases(self, inn: str) -> list:
        """Получает дела с kad.arbitr.ru"""
        all_cases = []
        page = 1

        try:
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                # Сначала заходим на главную чтобы получить куки
                try:
                    await session.get(
                        "https://kad.arbitr.ru/",
                        timeout=aiohttp.ClientTimeout(total=10)
                    )
                except Exception:
                    pass

                while True:
                    payload = {
                        "Page": page,
                        "Count": 25,
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

                    async with session.post(
                        KAD_API,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=30)
                    ) as resp:
                        if resp.status != 200:
                            logger.warning(f"kad.arbitr.ru статус {resp.status} стр.{page}")
                            break

                        data = await resp.json()
                        result = data.get("Result", {})
                        items = result.get("Items", [])

                        if not items:
                            break

                        for item in items:
                            case = self._parse_case_item(item)
                            if case:
                                all_cases.append(case)

                        # Проверяем есть ли ещё страницы
                        total = result.get("TotalCount", 0)
                        if page * 25 >= total:
                            break

                        page += 1
                        await asyncio.sleep(1)  # пауза между запросами

        except Exception as e:
            logger.error(f"Ошибка получения дел для {inn}: {e}")

        logger.info(f"Получено {len(all_cases)} дел для ИНН {inn}")
        return all_cases

    def _parse_case_item(self, item: dict) -> dict | None:
        try:
            case_id = str(item.get("CaseId") or item.get("Id") or "")
            number = item.get("CaseNumber") or item.get("Number") or ""
            court = item.get("CourtName") or ""
            status = item.get("StateName") or ""
            judge = item.get("JudgeName") or ""

            amount_raw = item.get("ClaimSum") or 0
            amount = f"{float(amount_raw):,.2f} ₽" if amount_raw else ""

            plaintiff, defendant = "", ""
            for side in (item.get("Sides") or []):
                t = (side.get("Type") or "").lower()
                n = side.get("Name") or ""
                if "истец" in t or "plaintiff" in t or t == "plaintiff":
                    plaintiff = n
                elif "ответчик" in t or "defendant" in t or t == "defendant":
                    defendant = n

            next_hearing = item.get("NextHearingDate") or ""
            if next_hearing and "T" in next_hearing:
                next_hearing = next_hearing.split("T")[0]

            documents = [
                doc.get("Name") or doc.get("Type") or ""
                for doc in (item.get("Documents") or [])
                if doc.get("Name") or doc.get("Type")
            ]

            return {
                "case_id": case_id,
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
            logger.error(f"Ошибка парсинга: {e}")
            return None

