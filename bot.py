import os
import sqlite3
import discord

from pathlib import Path
from datetime import datetime, date
from dotenv import load_dotenv
from discord.ext import commands, tasks
from discord import app_commands


# ==================================================
# 기본 설정
# ==================================================

BASE_DIR = Path(__file__).resolve().parent

load_dotenv(
    BASE_DIR / ".env",
    override=True
)

TOKEN = os.getenv("DISCORD_TOKEN")

# 여기에 네 포럼 채널 ID 입력
FORUM_CHANNEL_ID = 1548515209029746688


intents = discord.Intents.default()

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# ==================================================
# 데이터베이스
# ==================================================

db = sqlite3.connect(
    BASE_DIR / "games.db"
)

db.execute("""
CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    release_date TEXT NOT NULL,
    platform TEXT,
    price TEXT,
    comment TEXT,
    thread_id INTEGER NOT NULL,
    notified INTEGER DEFAULT 0
)
""")

db.commit()


columns = [
    row[1]
    for row in db.execute(
        "PRAGMA table_info(games)"
    ).fetchall()
]

if "price" not in columns:
    db.execute(
        "ALTER TABLE games ADD COLUMN price TEXT"
    )

if "comment" not in columns:
    db.execute(
        "ALTER TABLE games ADD COLUMN comment TEXT"
    )

if "notified" not in columns:
    db.execute(
        "ALTER TABLE games ADD COLUMN notified INTEGER DEFAULT 0"
    )

db.commit()


# ==================================================
# 공통 함수
# ==================================================

def make_thread_title(
    name,
    release_date
):
    return f"{name} | {release_date}"


def make_game_content(
    name,
    release_date,
    platform,
    price=None,
    comment=None
):
    content = (
        f"# {name}\n"
        f"────────────────\n\n"
        f"**출시일**\n"
        f"{release_date}\n\n"
        f"**플랫폼**\n"
        f"{platform}\n"
    )

    if price:
        content += (
            f"\n**가격**\n"
            f"{price}\n"
        )

    if comment:
        content += (
            f"\n**메모**\n"
            f"{comment}\n"
        )

    return content


def valid_release_date(
    release_date
):
    if release_date == "미정":
        return True

    try:
        datetime.strptime(
            release_date,
            "%Y-%m-%d"
        )
        return True

    except ValueError:
        return False


# ==================================================
# 봇 준비 완료
# ==================================================

@bot.event
async def on_ready():

    await bot.tree.sync()

    if not release_checker.is_running():
        release_checker.start()

    print(
        f"{bot.user} 로그인 완료"
    )

    print(
        "슬래시 명령어 동기화 완료"
    )


# ==================================================
# /게임추가
# ==================================================

@bot.tree.command(
    name="게임추가",
    description="출시 예정 게임을 등록합니다."
)
@app_commands.describe(
    게임명="게임 이름",
    출시일="YYYY-MM-DD 또는 미정",
    플랫폼="Steam, PS5, Xbox 등",
    가격="게임 가격 (선택)",
    하고싶은말="메모 (선택)"
)
async def add_game(
    interaction: discord.Interaction,
    게임명: str,
    출시일: str,
    플랫폼: str,
    가격: str | None = None,
    하고싶은말: str | None = None
):

    if not valid_release_date(
        출시일
    ):
        await interaction.response.send_message(
            "출시일은 `YYYY-MM-DD` 또는 `미정`으로 입력해주세요.\n"
            "예: `2026-11-19` / `미정`",
            ephemeral=True
        )
        return


    forum = bot.get_channel(
        FORUM_CHANNEL_ID
    )

    if not isinstance(
        forum,
        discord.ForumChannel
    ):
        await interaction.response.send_message(
            "포럼 채널을 찾을 수 없습니다.",
            ephemeral=True
        )
        return


    content = make_game_content(
        게임명,
        출시일,
        플랫폼,
        가격,
        하고싶은말
    )

    title = make_thread_title(
        게임명,
        출시일
    )


    result = await forum.create_thread(
        name=title,
        content=content
    )

    thread = result.thread


    db.execute(
        """
        INSERT INTO games (
            name,
            release_date,
            platform,
            price,
            comment,
            thread_id,
            notified
        )
        VALUES (?, ?, ?, ?, ?, ?, 0)
        """,
        (
            게임명,
            출시일,
            플랫폼,
            가격,
            하고싶은말,
            thread.id
        )
    )

    db.commit()


    await interaction.response.send_message(
        f"{게임명} 등록 완료",
        ephemeral=True
    )


# ==================================================
# /게임수정
# ==================================================

@bot.tree.command(
    name="게임수정",
    description="등록된 게임 정보를 수정합니다."
)
@app_commands.describe(
    게임명="수정할 게임 이름",
    출시일="새 출시일 (선택, YYYY-MM-DD 또는 미정)",
    플랫폼="새 플랫폼 (선택)",
    가격="새 가격 (선택)",
    하고싶은말="새 메모 (선택)"
)
async def edit_game(
    interaction: discord.Interaction,
    게임명: str,
    출시일: str | None = None,
    플랫폼: str | None = None,
    가격: str | None = None,
    하고싶은말: str | None = None
):

    game = db.execute(
        """
        SELECT
            id,
            name,
            release_date,
            platform,
            price,
            comment,
            thread_id,
            notified
        FROM games
        WHERE name = ?
        ORDER BY id ASC
        LIMIT 1
        """,
        (게임명,)
    ).fetchone()


    if not game:
        await interaction.response.send_message(
            f"{게임명}을(를) 찾을 수 없습니다.",
            ephemeral=True
        )
        return


    (
        game_id,
        old_name,
        old_date,
        old_platform,
        old_price,
        old_comment,
        thread_id,
        old_notified
    ) = game


    if 출시일 is not None:

        if not valid_release_date(
            출시일
        ):
            await interaction.response.send_message(
                "출시일은 `YYYY-MM-DD` 또는 `미정`이어야 합니다.",
                ephemeral=True
            )
            return


    new_date = (
        출시일
        if 출시일 is not None
        else old_date
    )

    new_platform = (
        플랫폼
        if 플랫폼 is not None
        else old_platform
    )

    new_price = (
        가격
        if 가격 is not None
        else old_price
    )

    new_comment = (
        하고싶은말
        if 하고싶은말 is not None
        else old_comment
    )

    new_notified = (
        0
        if 출시일 is not None
        else old_notified
    )


    db.execute(
        """
        UPDATE games
        SET
            release_date = ?,
            platform = ?,
            price = ?,
            comment = ?,
            notified = ?
        WHERE id = ?
        """,
        (
            new_date,
            new_platform,
            new_price,
            new_comment,
            new_notified,
            game_id
        )
    )

    db.commit()


    try:

        thread = bot.get_channel(
            thread_id
        )

        if thread is None:
            thread = await bot.fetch_channel(
                thread_id
            )

        # 포럼 제목도 같이 수정
        new_title = make_thread_title(
            old_name,
            new_date
        )

        await thread.edit(
            name=new_title
        )


        starter_message = await thread.fetch_message(
            thread_id
        )

        new_content = make_game_content(
            old_name,
            new_date,
            new_platform,
            new_price,
            new_comment
        )

        await starter_message.edit(
            content=new_content
        )

    except Exception as e:

        print(
            f"포럼 게시글 수정 실패: {e}"
        )


    await interaction.response.send_message(
        f"{게임명} 수정 완료",
        ephemeral=True
    )


# ==================================================
# /게임삭제
# ==================================================

@bot.tree.command(
    name="게임삭제",
    description="등록된 게임을 삭제합니다."
)
@app_commands.describe(
    게임명="삭제할 게임 이름"
)
async def delete_game(
    interaction: discord.Interaction,
    게임명: str
):

    game = db.execute(
        """
        SELECT
            id,
            thread_id
        FROM games
        WHERE name = ?
        ORDER BY id ASC
        LIMIT 1
        """,
        (게임명,)
    ).fetchone()


    if not game:

        await interaction.response.send_message(
            f"{게임명}을(를) 찾을 수 없습니다.",
            ephemeral=True
        )
        return


    game_id, thread_id = game


    try:

        thread = bot.get_channel(
            thread_id
        )

        if thread is None:
            thread = await bot.fetch_channel(
                thread_id
            )

        await thread.delete()

    except Exception as e:

        print(
            f"포럼 게시글 삭제 실패: {e}"
        )


    db.execute(
        """
        DELETE FROM games
        WHERE id = ?
        """,
        (game_id,)
    )

    db.commit()


    await interaction.response.send_message(
        f"{게임명} 삭제 완료",
        ephemeral=True
    )


# ==================================================
# /출시예정
# ==================================================

@bot.tree.command(
    name="출시예정",
    description="등록된 출시 예정 게임 목록을 보여줍니다."
)
async def upcoming_games(
    interaction: discord.Interaction
):

    games = db.execute(
        """
        SELECT
            name,
            release_date,
            platform,
            price
        FROM games
        """
    ).fetchall()


    if not games:

        await interaction.response.send_message(
            "등록된 게임이 없습니다.",
            ephemeral=True
        )
        return


    today = date.today()

    dated_games = []
    unknown_games = []


    for (
        name,
        release_date,
        platform,
        price
    ) in games:

        if release_date == "미정":

            unknown_games.append(
                (
                    name,
                    platform,
                    price
                )
            )

            continue


        try:

            release = datetime.strptime(
                release_date,
                "%Y-%m-%d"
            ).date()

            dated_games.append(
                (
                    release,
                    name,
                    release_date,
                    platform,
                    price
                )
            )

        except ValueError:

            unknown_games.append(
                (
                    name,
                    platform,
                    price
                )
            )


    dated_games.sort(
        key=lambda x: x[0]
    )


    message = (
        "# 출시 예정 게임\n"
        "────────────────\n\n"
    )


    for (
        release,
        name,
        release_date,
        platform,
        price
    ) in dated_games:

        days = (
            release - today
        ).days


        if days > 0:
            dday = f"D-{days}"

        elif days == 0:
            dday = "오늘 출시"

        else:
            dday = "출시됨"


        message += (
            f"**{name}**\n"
            f"{release_date} · {dday}\n"
            f"{platform}\n"
        )

        if price:
            message += (
                f"{price}\n"
            )

        message += "\n"


    if unknown_games:

        message += (
            "## 출시일 미정\n"
            "────────────────\n\n"
        )

        for (
            name,
            platform,
            price
        ) in unknown_games:

            message += (
                f"**{name}**\n"
                f"미정\n"
                f"{platform}\n"
            )

            if price:
                message += (
                    f"{price}\n"
                )

            message += "\n"


    if len(message) > 1900:

        message = (
            message[:1900]
            + "\n\n목록이 너무 길어 일부만 표시합니다."
        )


    await interaction.response.send_message(
    message
    )


# ==================================================
# 출시일 자동 알림
# ==================================================

@tasks.loop(minutes=30)
async def release_checker():

    today = date.today().isoformat()


    games = db.execute(
        """
        SELECT
            id,
            name,
            thread_id
        FROM games
        WHERE release_date = ?
        AND notified = 0
        """,
        (today,)
    ).fetchall()


    for (
        game_id,
        name,
        thread_id
    ) in games:

        try:

            thread = bot.get_channel(
                thread_id
            )

            if thread is None:

                thread = await bot.fetch_channel(
                    thread_id
                )


            await thread.send(
                f"# 출시일 도래\n"
                f"────────────────\n\n"
                f"**{name}**이(가) 오늘 출시됩니다."
            )


            db.execute(
                """
                UPDATE games
                SET notified = 1
                WHERE id = ?
                """,
                (game_id,)
            )

            db.commit()


        except Exception as e:

            print(
                f"{name} 출시 알림 실패: {e}"
            )


@release_checker.before_loop
async def before_release_checker():

    await bot.wait_until_ready()


# ==================================================
# 실행
# ==================================================

if not TOKEN:

    print(
        ".env에서 DISCORD_TOKEN을 찾지 못했습니다."
    )

else:

    bot.run(TOKEN)