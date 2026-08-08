import asyncio
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from app.auth import create_session_token, hash_token
from app.config import get_settings
from app.db import SessionLocal, User, UserSession


async def run_test():
    settings = get_settings()
    db = SessionLocal()

    user = db.scalar(select(User).where(User.email == "int_weather@test.com"))
    if not user:
        user = User(email="int_weather@test.com", password_hash="hashed")
        db.add(user)
        db.commit()

    raw_token = create_session_token()
    session = UserSession(
        user_id=user.id,
        token_hash=hash_token(raw_token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.add(session)
    db.commit()

    cookie_name = settings.session_cookie
    cookies = {cookie_name: raw_token}

    log_file = "/tmp/opencode/aichat.log"
    try:
        with open(log_file, "rb") as f:
            f.seek(0, 2)
            log_start_pos = f.tell()
    except FileNotFoundError:
        log_start_pos = 0

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8080", cookies=cookies, timeout=30.0
    ) as client:
        print("Asking: Какая погода в Минске?")
        response = await client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "Какая погода в Минске?"}],
                "stream": False,
            },
        )

    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            f.seek(log_start_pos)
            request_logs = f.read()
    except Exception:
        request_logs = "Could not read logs"

    if response.status_code != 200:
        print(f"Error: {response.status_code} {response.text}")
        print("\n--- Server Logs ---\n", request_logs, "\n------------------")
        return

    data = response.json()
    content = data["choices"][0]["message"]["content"]
    print(f"Response: {content}")

    success = False
    if "OpenWeatherMap" not in content:
        print("FAIL: Missing attribution to OpenWeatherMap")
    elif "{" in content and "}" in content:
        print("FAIL: Response contains placeholders like {температура}")
    elif any(char.isdigit() for char in content):
        print("SUCCESS: Response contains actual data")
        success = True
    else:
        print("FAIL: No numeric data found in response")

    if not success:
        print("\n--- Server Logs (Internal Flow) ---\n", request_logs, "\n------------------")


if __name__ == "__main__":
    asyncio.run(run_test())
