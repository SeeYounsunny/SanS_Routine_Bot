import anthropic
from collections import defaultdict

client = anthropic.AsyncAnthropic()


async def generate_summary(routines: list[dict], date: str) -> str:
    # 사람별로 그룹핑
    by_user: dict[str, dict] = defaultdict(lambda: {"morning": [], "evening": []})
    for r in routines:
        by_user[r["user_name"]][r["routine_type"]].append(r["content"])

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

    message = await client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    return message.content[0].text
