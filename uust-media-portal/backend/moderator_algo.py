import re
from typing import List, Dict

UUST_ACRONYMS_WHITELIST = {
    "УУНИТ", "ИИМРТ", "ФАДЭТ", "ПИШ", "ВУЦ", "НОЦ", "СЭО",
    "ФТИ", "ИНЭБ", "ИЭТИ", "ИГСН", "ИИГУ", "ИХЗЧС", "ИТМ",
    "УМПО", "ОДК", "ВКС", "СВО", "ГОСТ", "IT", "ИТ", "РФ", "РБ"
}


def inspect_article_non_ai(title: str, body: str, stop_words: List[str]) -> Dict:
    full_text = f"{title} {body}"
    issues: List[str] = []

    found_stop = [
        w for w in stop_words
        if w.strip() and w.strip().lower() in full_text.lower()
    ]
    if found_stop:
        issues.append(f"Стоп-слова: {', '.join(found_stop)}")

    caps_tokens = re.findall(r"[А-ЯЁA-Z]{4,}", full_text)
    caps_violations = sorted({
        tok for tok in caps_tokens
        if tok.upper() not in UUST_ACRONYMS_WHITELIST
    })
    if caps_violations:
        issues.append(f"Злоупотребление CAPS LOCK: {', '.join(caps_violations)}")

    if re.search(r"[!?]{2,}", full_text):
        issues.append("Избыточная пунктуация (!! или ??)")

    if re.search(r"  +", full_text):
        issues.append("Двойные пробелы в тексте")

    return {
        "clean": len(issues) == 0,
        "issues": issues,
        "found_stop_words": found_stop,
        "caps_violations": caps_violations,
    }