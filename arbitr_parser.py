import aiohttp
import asyncio
import logging
import json
import re
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS_BROWSER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

HEADERS_KAD = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://kad.arbitr.ru",
    "Referer": "https://kad.arbitr.ru/",
    "X-Requested-With": "XMLHttpRequest",
}


class ArbitrParser:

    async def get_company_name(self, inn: str) -> str | None:
        """Пробует несколько источников для получения названия компании"""
        # 1. ЕГРЮЛ
        name = await self._name_from_egrul(inn)
        if name and name != inn:
            return name

        # 2. Rusprofile
        name = await self._name_from_rusprofile(inn)
        if name and name != inn:
            return name

        # Если ничего не нашли — возвращаем ИНН (бот не упадёт)
        return inn

    async def get_cases(self, inn: str) -> list:
        """Пробует несколько источников для получения дел"""

        # 1. kad.arbitr.ru (официальный)
        logger.info(f"[{inn}] Пробую kad.arbitr.ru...")
        cases = await self._cases_from_kad(inn)
        if cases:
            logger.info(f"[{inn}] kad.arbitr.ru: {len(cases)} дел")
            return cases

        # 2. zachestnyibiznes.ru
        logger.info(f"[{inn}] Пробую zachestnyibiznes.ru...")
        cases = await self._cases_from_zachestny(inn)
        if cases:
            logger.info(f"[{inn}] zachestnyibiznes.ru: {len(cases)} дел")
            return cases

        # 3. rusprofile.ru
        logger.info(f"[{inn}] Пробую rusprofile.ru...")
        cases = await self._cases_from_rusprofile(inn)
        if cases:
            logger.info(f"[{inn}] rusprofile.ru: {len(cases)} дел")
            return cases

        logger.warning(f"[{inn}] Все источники вернули 0 дел")
        return []

    # ─────────────────────────────────────────
    # ПОЛУЧЕНИЕ НАЗВАНИЯ КОМПАНИИ
    # ─────────────────────────────────────────

    async def _name_from_egrul(self, inn: str) -> str | None:
        try:
            async with aiohttp.ClientSession(headers=HEADERS_BROWSER) as session:
                await session.get("https://egrul.nalog.ru/", timeout=aiohttp.ClientTimeout(total=10))
                async with session.post(
                    "https://egrul.nalog.ru/search-show",
                    data={"query": inn, "region": "", "page": ""},
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    rows = data.get("rows", [])
                    if not rows:
                        return None
                    return rows[0].get("n") or rows[0].get("c")
        except Exception as e:
            logger.error(f"ЕГРЮЛ ошибка для {inn}: {e}")
            return None

    async def _name_from_rusprofile(self, inn: str) -> str | None:
        try:
            url = f"https://www.rusprofile.ru/search?query={inn}&type=ul"
            async with aiohttp.ClientSession(headers=HEADERS_BROWSER) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        return None
                    html = await resp.text()
                    soup = BeautifulSoup(html, "html.parser")
                    # Ищем название компании
                    el = soup.select_one(".company-name") or soup.select_one("h1.title")
                    if el:
                        return el.get_text(strip=True)
                    return None
        except Exception as e:
            logger.error(f"Rusprofile name ошибка для {inn}: {e}")
            return None

    # ─────────────────────────────────────────
    # ПАРСЕРЫ ДЕЛ
    # ─────────────────────────────────────────

    async def _cases_from_kad(self, inn: str) -> list:
        """Официальный API kad.arbitr.ru"""
        all_cases = []
        page = 1
        try:
            async with aiohttp.ClientSession(headers=HEADERS_KAD) as session:
                try:
                    await session.get("https://kad.arbitr.ru/", timeout=aiohttp.ClientTimeout(total=10))
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
                        "https://kad.arbitr.ru/Kad/SearchInstances",
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=30)
                    ) as resp:
                        if resp.status != 200:
                            break
                        data = await resp.json()
                        result = data.get("Result", {})
                        items = result.get("Items", [])
                        if not items:
                            break
                        for item in items:
                            case = self._parse_kad_item(item)
                            if case:
                                all_cases.append(case)
                        total = result.get("TotalCount", 0)
                        if page * 25 >= total or page >= 10:
                            break
                        page += 1
                        await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"kad.arbitr.ru ошибка: {e}")
        return all_cases

    async def _cases_from_zachestny(self, inn: str) -> list:
        """zachestnyibiznes.ru — парсинг страницы компании"""
        cases = []
        try:
            url = f"https://zachestnyibiznes.ru/company/ul/{inn}"
            async with aiohttp.ClientSession(headers=HEADERS_BROWSER) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status != 200:
                        return []
                    html = await resp.text()

            soup = BeautifulSoup(html, "html.parser")

            # Ищем блок с арбитражными делами
            arbitr_section = soup.find("div", {"id": "arbitr"}) or soup.find("section", string=re.compile("арбитр", re.I))
            if not arbitr_section:
                # Пробуем найти таблицу с делами
                arbitr_section = soup.find("table", class_=re.compile("arbitr|court|case", re.I))

            if not arbitr_section:
                return []

            rows = arbitr_section.find_all("tr")
            for row in rows[1:]:  # пропускаем заголовок
                cols = row.find_all("td")
                if len(cols) < 2:
                    continue
                number = cols[0].get_text(strip=True)
                if not number:
                    continue
                # Извлекаем ссылку на дело
                link = cols[0].find("a")
                case_id = ""
                if link and link.get("href"):
                    href = link["href"]
                    m = re.search(r'/Card/([^/?]+)', href)
                    if m:
                        case_id = m.group(1)

                status = cols[-1].get_text(strip=True) if len(cols) > 2 else ""
                cases.append({
                    "case_id": case_id or number,
                    "number": number,
                    "court": cols[1].get_text(strip=True) if len(cols) > 1 else "",
                    "status": status,
                    "judge": "",
                    "amount": "",
                    "plaintiff": "",
                    "defendant": "",
                    "next_hearing": "",
                    "documents": [],
                })
        except Exception as e:
            logger.error(f"zachestnyibiznes ошибка: {e}")
        return cases

    async def _cases_from_rusprofile(self, inn: str) -> list:
        """rusprofile.ru — парсинг арбитражных дел"""
        cases = []
        try:
            url = f"https://www.rusprofile.ru/arbitration/{inn}"
            async with aiohttp.ClientSession(headers=HEADERS_BROWSER) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status != 200:
                        return []
                    html = await resp.text()

            soup = BeautifulSoup(html, "html.parser")

            # Ищем карточки дел
            cards = soup.select(".arbitration-item, .court-case, [class*='arbitr']")
            if not cards:
                # Пробуем найти таблицу
                table = soup.find("table")
                if table:
                    for row in table.find_all("tr")[1:]:
                        cols = row.find_all("td")
                        if len(cols) < 2:
                            continue
                        number = cols[0].get_text(strip=True)
                        if not number:
                            continue
                        cases.append({
                            "case_id": number,
                            "number": number,
                            "court": cols[1].get_text(strip=True) if len(cols) > 1 else "",
                            "status": cols[-1].get_text(strip=True) if len(cols) > 2 else "",
                            "judge": "",
                            "amount": "",
                            "plaintiff": "",
                            "defendant": "",
                            "next_hearing": "",
                            "documents": [],
                        })
                return cases

            for card in cards:
                number_el = card.select_one(".case-number, .number, a")
                number = number_el.get_text(strip=True) if number_el else ""
                if not number:
                    continue
                status_el = card.select_one(".status, .state")
                status = status_el.get_text(strip=True) if status_el else ""
                cases.append({
                    "case_id": number,
                    "number": number,
                    "court": "",
                    "status": status,
                    "judge": "",
                    "amount": "",
                    "plaintiff": "",
                    "defendant": "",
                    "next_hearing": "",
                    "documents": [],
                })
        except Exception as e:
            logger.error(f"rusprofile ошибка: {e}")
        return cases

    # ─────────────────────────────────────────
    # ВСПОМОГАТЕЛЬНЫЕ
    # ─────────────────────────────────────────

    def _parse_kad_item(self, item: dict) -> dict | None:
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
                if "истец" in t or "plaintiff" in t:
                    plaintiff = n
                elif "ответчик" in t or "defendant" in t:
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
            logger.error(f"Ошибка парсинга kad item: {e}")
            return None
