import os
import anthropic
from collections import defaultdict

# 모델 이름을 환경변수로도 바꿀 수 있게 함 (없으면 기본값 사용)
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-3-sonnet-20240229")
client = anthropic.AsyncAnthropic()


async def generate_summary(routines: list[dict], date: str) -> str:
    # 사람별로 그룹핑 (키/값 None 방지)
    by_user: dict[str, dict] = defaultdict(lambda: {"morning": [], "evening": []})
    for r in routines:
        name = (r.get("user_name") or "").strip() or "이름 없음"
        rtype = (r.get("routine_type") or "morning").strip() or "morning"
        content = (r.get("content") or "").strip()
        by_user[name][rtype].append(content)

    # 프롬프트용 텍스트 구성
    date_label = f"{date[:4]}년 {date[5:7]}월 {date[8:]}일"
    content_block = ""
    for name, data in by_user.items():
        content_block += f"### {name}\n"
        if data["morning"]:
            content_block += "아침 계획:\n" + "\n".join(data["morning"]) + "\n"
        else:
            content_block += "아침 계획: (미입력)\n"
        if data["evening"]:
            content_block += "저녁 회고:\n" + "\n".join(data["evening"]) + "\n"
        else:
            content_block += "저녁 회고: (미입력)\n"
        content_block += "\n"

    prompt = f"""아래는 텔레그램 그룹 멤버들의 {date_label} 루틴 기록입니다.
각 멤버별로 오늘의 계획과 회고를 간결하고 따뜻하게 요약해주세요.
이모지를 활용해서 읽기 좋게 정리해주세요.
마지막에 그룹 전체에 짧은 응원 메시지를 한 줄 추가해주세요.

{content_block}

형식 예시:
📊 *{date_label} 루틴 요약*

👤 *[이름]*
• 오늘 계획: [요약]
• 오늘 회고: [요약 or ⏳ 미입력]

(반복)

💬 *오늘의 한마디*: [응원 메시지]
"""

    try:
        message = await client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text
    except Exception as e:
        err = str(e).lower()
        if "not_found" in err or "404" in err:
            raise ValueError(
                "요약용 AI 모델을 찾을 수 없어요. Railway에서 ANTHROPIC_MODEL 값을 확인해 주세요."
            )
        if "rate" in err or "429" in err:
            raise ValueError(
                "AI 요청 한도를 다 썼어요. 잠시 후 다시 시도하거나 유료 크레딧을 확인해 주세요."
            )
        if "authentication" in err or "401" in err or "invalid" in err:
            raise ValueError("AI API 키가 올바르지 않아요. ANTHROPIC_API_KEY를 확인해 주세요.")
        raise
