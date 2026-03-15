import os
import anthropic
from collections import defaultdict

# ANTHROPIC_MODEL이 있으면 그 모델만 사용. 없으면 아래 목록을 순서대로 시도 (404 시 다음 모델).
MODEL_FALLBACK_LIST = [
    "claude-3-5-sonnet-20241022",
    "claude-sonnet-4-20250514",
    "claude-3-sonnet-20240229",
    "claude-haiku-4-5-20251001",
]
client = anthropic.AsyncAnthropic()


async def generate_summary(routines: list[dict], date: str) -> str:
    # 사람별로 그룹핑 (키/값 None 방지)
    by_user: dict[str, dict] = defaultdict(lambda: {"morning": [], "evening": []})
    for r in routines:
        name = (r.get("user_name") or "").strip() or "이름 없음"
        rtype = (r.get("routine_type") or "morning").strip() or "morning"
        content = (r.get("content") or "").strip()
        by_user[name][rtype].append(content)

    # 프롬프트용 텍스트 구성 (오늘 루틴 하나로 통합, 저녁 없으면 제외)
    date_label = f"{date[:4]}년 {date[5:7]}월 {date[8:]}일"
    content_block = ""
    for name, data in by_user.items():
        all_items = list(data["morning"]) + list(data["evening"])
        content_block += f"### {name}\n"
        if all_items:
            content_block += "오늘 루틴: " + ", ".join(all_items) + "\n\n"
        else:
            content_block += "오늘 루틴: (미입력)\n\n"

    prompt = f"""아래는 텔레그램 그룹 멤버들의 {date_label} 루틴 기록입니다.
각 멤버별로 오늘 루틴을 하나로 묶어서 간결하고 따뜻하게 요약해주세요.
계획/회고로 나누지 말고, "오늘 루틴" 한 줄로만 요약해주세요.
이모지를 활용해서 읽기 좋게 정리해주세요.
마지막에 그룹 전체에 짧은 응원 메시지를 한 줄 추가해주세요.

{content_block}

형식 예시:
📊 *{date_label} 루틴 요약*

👤 *[이름]*
• 오늘 루틴: [요약]

(반복)

💬 *오늘의 한마디*: [응원 메시지]
"""

    model_list = [os.environ.get("ANTHROPIC_MODEL")] if os.environ.get("ANTHROPIC_MODEL") else MODEL_FALLBACK_LIST
    for model in model_list:
        if not model:
            continue
        try:
            message = await client.messages.create(
                model=model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text
        except Exception as e:
            err = str(e).lower()
            if "not_found" in err or "404" in err:
                continue
            if "rate" in err or "429" in err:
                raise ValueError(
                    "AI 요청 한도를 다 썼어요. 잠시 후 다시 시도하거나 유료 크레딧을 확인해 주세요."
                )
            if "authentication" in err or "401" in err or "invalid" in err:
                raise ValueError("AI API 키가 올바르지 않아요. ANTHROPIC_API_KEY를 확인해 주세요.")
            raise
    raise ValueError(
        "요약용 AI 모델을 찾을 수 없어요. Anthropic 콘솔에서 사용 가능한 모델 ID를 확인한 뒤 "
        "Railway Variables에 ANTHROPIC_MODEL로 설정해 주세요."
    )
