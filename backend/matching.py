def match_notice(profile, notice):
    """Return the rules a notice passes, or None when it is excluded."""
    eligibility = notice.get("eligibility", {})
    licenses = eligibility.get("licenses", [])
    regions = eligibility.get("regions", [])
    budget = notice.get("budget")

    if not licenses or not set(licenses).issubset(profile["licenses"]):
        return None
    if profile["region"] not in regions:
        return None
    if type(budget) is not int or not profile["min_budget"] <= budget <= profile["max_budget"]:
        return None

    return [
        f"필수 면허 보유 ({', '.join(licenses)})",
        f"허용 지역에 소재 ({profile['region']})",
        f"희망 금액대 충족 ({budget:,}원)",
    ]
