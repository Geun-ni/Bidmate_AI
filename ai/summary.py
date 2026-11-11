SUMMARY_LABELS = {
    "project": "사업명",
    "budget": "금액",
    "schedule": "일정",
    "eligibility": "입찰참가자격",
    "evaluation": "평가 기준",
}


def summarize_notice(notice):
    """Prepare the sample summary and checklist for display."""
    summary = []
    for key, label in SUMMARY_LABELS.items():
        item = notice.get("summary", {}).get(key, {})
        source = item.get("source")
        summary.append({
            "label": label,
            "value": item.get("value", "확인 필요") if source else "확인 필요",
            "source": source,
        })

    return {"summary": summary, "documents": notice.get("documents", [])}
