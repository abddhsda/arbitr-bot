async def get_company_name(self, inn: str) -> str | None:
    """Получает название компании по ИНН через ЕГРЮЛ"""
    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            # Шаг 1: получаем token
            async with session.get(
                f"https://egrul.nalog.ru/",
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                pass

            # Шаг 2: поиск
            async with session.post(
                "https://egrul.nalog.ru/search-show",
                data={"query": inn, "region": "", "page": ""},
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    return inn  # возвращаем ИНН если не нашли
                data = await resp.json()
                items = data.get("rows", [])
                if not items:
                    return inn
                name = items[0].get("n") or items[0].get("c") or inn
                return name
    except Exception as e:
        logger.error(f"Ошибка получения названия компании {inn}: {e}")
        return inn  # возвращаем ИНН как запасной вариант
