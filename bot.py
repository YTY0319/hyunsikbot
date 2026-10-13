import os
import sqlite3
import discord

from pathlib import Path
from datetime import datetime, date
from dotenv import load_dotenv
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import io

from collections import Counter
from PIL import Image, ImageDraw
from urllib.parse import quote

def get_character_icon_path(code):
    if code is None:
        return None

    path = CHARACTER_ICON_DIR / f"{code}.png"

    if path.exists():
        return path

    return None


# ==================================================
# 기본 설정
# ==================================================


BASE_DIR = Path(__file__).resolve().parent

load_dotenv(
    BASE_DIR / ".env",
    override=True
)
CHARACTER_ICON_DIR = BASE_DIR / "assets" / "characters"

TOKEN = os.getenv("DISCORD_TOKEN")
ER_API_KEY = os.getenv("ER_API_KEY")

ER_BASE_URL = "https://open-api.bser.io"

character_names = {}

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

DB_PATH = os.getenv(
    "RAILWAY_VOLUME_MOUNT_PATH",
    str(BASE_DIR)
)

db = sqlite3.connect(
    Path(DB_PATH) / "games.db"
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
# 이터널 리턴 API 공통 함수
# ==================================================

async def er_request(session, endpoint):
    headers = {
        "x-api-key": ER_API_KEY
    }

    async with session.get(
        f"{ER_BASE_URL}{endpoint}",
        headers=headers
    ) as response:

        try:
            data = await response.json()
        except Exception:
            text = await response.text()
            print(
                f"ER API JSON 오류 | "
                f"HTTP {response.status} | "
                f"{endpoint} | {text}"
            )
            return None

        if response.status != 200:
            print(
                f"ER API HTTP 오류 | "
                f"HTTP {response.status} | "
                f"{endpoint} | {data}"
            )
            return None

        if data.get("code") != 200:
            print(
                f"ER API 응답 오류 | "
                f"{endpoint} | {data}"
            )
            return None

        return data


async def get_er_user(session, nickname):
    data = await er_request(
        session,
        f"/v1/user/nickname?query={nickname}"
    )

    if not data:
        return None

    return data.get("user")


async def load_character_names(
    session
):
    global character_names

    # 이미 불러왔다면 다시 요청하지 않음
    if character_names:
        return character_names

    metadata = await er_request(
        session,
        "/v1/l10n/Korean"
    )

    if not metadata:
        return {}

    try:
        l10n_url = metadata["data"]["l10Path"]
    except (KeyError, TypeError):
        return {}

    async with session.get(
        l10n_url
    ) as response:

        if response.status != 200:
            return {}

        text = await response.text()

    result = {}

    for line in text.splitlines():

        if "┃" not in line:
            continue

        key, value = line.split(
            "┃",
            1
        )

        if key.startswith(
            "Character/Name/"
        ):

            try:
                code = int(
                    key.split("/")[-1]
                )

                result[code] = value

            except ValueError:
                pass

    character_names = result

    return result


def get_character_name(
    code
):
    return character_names.get(
        code,
        f"캐릭터 {code}"
    )


# ==================================================
# RP 그래프 생성
# ==================================================

def make_rp_graph(
    scores, most_three=None
):
    width = 800
    height = 500

    margin_left = 80
    margin_right = 40
    margin_top = 50
    margin_bottom = 150

    image = Image.new(
        "RGB",
        (width, height),
        "white"
    )

    draw = ImageDraw.Draw(
        image
    )

    # 데이터가 너무 적으면 그래프 생성 X
    if len(scores) < 2:
        return None

    minimum = min(scores)
    maximum = max(scores)

    if minimum == maximum:
        minimum -= 100
        maximum += 100

    padding = max(
        50,
        int(
            (maximum - minimum) * 0.15
        )
    )

    minimum -= padding
    maximum += padding

    graph_width = (
        width
        - margin_left
        - margin_right
    )

    graph_height = (
        height
        - margin_top
        - margin_bottom
    )

    # 제목
    draw.text(
        (margin_left, 15),
        "RP Trend",
        fill="black"
    )

    # 가로 눈금선
    for i in range(5):

        ratio = i / 4

        y = (
            margin_top
            + graph_height * ratio
        )

        value = int(
            maximum
            - (maximum - minimum) * ratio
        )

        draw.line(
            (
                margin_left,
                y,
                width - margin_right,
                y
            ),
            fill=(220, 220, 220),
            width=1
        )

        draw.text(
            (10, y - 7),
            str(value),
            fill="black"
        )

    points = []

    for i, score in enumerate(
        scores
    ):

        if len(scores) == 1:
            x = margin_left
        else:
            x = (
                margin_left
                + graph_width
                * i
                / (len(scores) - 1)
            )

        score_ratio = (
            (score - minimum)
            / (maximum - minimum)
        )

        y = (
            margin_top
            + graph_height
            * (1 - score_ratio)
        )

        points.append(
            (x, y)
        )

    # 선
    for i in range(
        len(points) - 1
    ):

        draw.line(
            (
                points[i][0],
                points[i][1],
                points[i + 1][0],
                points[i + 1][1]
            ),
            fill="black",
            width=3
        )

    # 점 / 점수 표시
    for i, (
        point,
        score
    ) in enumerate(
        zip(points, scores)
    ):

        x, y = point

        radius = 5

        draw.ellipse(
            (
                x - radius,
                y - radius,
                x + radius,
                y + radius
            ),
            fill="black"
        )

        draw.text(
            (
                x - 12,
                y - 23
            ),
            str(score),
            fill="black"
        )

        draw.text(
            (
                x - 3,
                height - 45
            ),
            str(i + 1),
            fill="black"
        )

    buffer = io.BytesIO()

        # 모스트 3 캐릭터
    if most_three:

        start_x = 120
        icon_y = 330
        icon_size = 56
        gap = 220

        for i, char in enumerate(most_three[:3]):

            code = (
                char.get("characterCode")
                or char.get("characterNum")
            )

            name = get_character_name(code)

            games_played = char.get(
                "totalGames",
                0
            )

            wins = char.get(
                "wins",
                0
            )

            if games_played > 0:
                win_rate = (
                    wins
                    / games_played
                    * 100
                )
            else:
                win_rate = 0

            x = start_x + (i * gap)

            icon_path = get_character_icon_path(
                code
            )

            if icon_path:

                try:
                    icon = Image.open(
                        icon_path
                    ).convert("RGBA")

                    icon = icon.resize(
                        (
                            icon_size,
                            icon_size
                        )
                    )

                    image.paste(
                        icon,
                        (x, icon_y),
                        icon
                    )

                except Exception as e:
                    print(
                        f"캐릭터 아이콘 오류: {e}"
                    )

            draw.text(
                (
                    x + 68,
                    icon_y
                ),
                name,
                fill="black"
            )

            draw.text(
                (
                    x + 68,
                    icon_y + 20
                ),
                f"{games_played}게임"
                f" / {win_rate:.0f}%",
                fill="black"
            )

    image.save(
        buffer,
        format="PNG"
    )

    buffer.seek(0)

    return buffer


# ==================================================
# /전적
# ==================================================

@bot.tree.command(
    name="전적",
    description="이터널 리턴 최근 전적을 조회합니다."
)
@app_commands.describe(
    닉네임="이터널 리턴 닉네임"
)
async def er_record(
    interaction: discord.Interaction,
    닉네임: str
):

    await interaction.response.defer()

    if not ER_API_KEY:

        await interaction.followup.send(
            "ER_API_KEY가 설정되어 있지 않습니다."
        )

        return

    async with aiohttp.ClientSession() as session:

        user = await get_er_user(
            session,
            닉네임
        )

        if not user:

            await interaction.followup.send(
                f"{닉네임} 유저를 찾을 수 없습니다."
            )

            return

        uid = user.get("userId") or user.get("uid")

        if not uid:
            await interaction.followup.send(
                "유저 ID를 가져오지 못했습니다."
            )
            return

        real_name = user.get(
            "nickname",
            닉네임
        )

        games_data = await er_request(
            session,
            f"/v1/user/games/uid/{uid}"
        )

        if not games_data:

            await interaction.followup.send(
                "전적 정보를 불러오지 못했습니다."
            )

            return

        await load_character_names(
            session
        )

    games = games_data.get(
        "userGames",
        []
    )

    if not games:

        await interaction.followup.send(
            f"{real_name}의 최근 전적이 없습니다."
        )

        return

    recent_games = games[:5]

    message = (
        f"# {real_name}\n"
        f"최근 전적\n"
        f"────────────────\n\n"
    )

    for index, game in enumerate(
        recent_games,
        start=1
    ):

        character_code = game.get(
            "characterNum"
        )

        character = get_character_name(
            character_code
        )

        rank = game.get(
            "gameRank",
            "-"
        )

        kills = game.get(
            "playerKill",
            0
        )

        assists = game.get(
            "playerAssistant",
            0
        )

        mode = game.get(
            "matchingMode"
        )

        if mode == 3:
            mode_name = "랭크"

        elif mode == 2:
            mode_name = "일반"

        elif mode == 6:
            mode_name = "코발트"

        else:
            mode_name = "기타"

        message += (
            f"**{index}. {character}**\n"
            f"{mode_name} · {rank}위\n"
            f"{kills}킬 / {assists}어시스트\n"
        )

        if mode == 3:

            mmr_gain = game.get(
                "mmrGainGambit"
            )

            if mmr_gain is None:
                mmr_gain = game.get(
                    "mmrGain"
                )

            if mmr_gain is not None:

                sign = (
                    "+"
                    if mmr_gain > 0
                    else ""
                )

                message += (
                    f"RP {sign}{mmr_gain}\n"
                )

        message += "\n"

    await interaction.followup.send(
        message
    )


# ==================================================
# /점수
# ==================================================

# ==================================================
# /점수
# ==================================================

@bot.tree.command(
    name="점수",
    description="이터널 리턴 랭크 점수와 통계를 조회합니다."
)
@app_commands.describe(
    닉네임="이터널 리턴 닉네임"
)
async def er_rating(
    interaction: discord.Interaction,
    닉네임: str
):

    await interaction.response.defer()

    if not ER_API_KEY:
        await interaction.followup.send(
            "ER_API_KEY가 설정되어 있지 않습니다."
        )
        return

    async with aiohttp.ClientSession() as session:

        # ------------------------------
        # 유저 조회
        # ------------------------------

        user = await get_er_user(
            session,
            닉네임
        )

        if not user:
            await interaction.followup.send(
                f"{닉네임} 유저를 찾을 수 없습니다."
            )
            return

        uid = (
            user.get("userId")
            or user.get("uid")
        )

        if not uid:
            await interaction.followup.send(
                "유저 ID를 가져오지 못했습니다."
            )
            return

        real_name = user.get(
            "nickname",
            닉네임
        )

        # ------------------------------
        # 최근 전적
        # ------------------------------

        games_data = await er_request(
            session,
            f"/v1/user/games/uid/{uid}"
        )

        if not games_data:
            await interaction.followup.send(
                "전적 정보를 불러오지 못했습니다."
            )
            return

        games = games_data.get(
            "userGames",
            []
        )

        ranked_games = [
            game
            for game in games
            if game.get("matchingMode") == 3
        ]

        if not ranked_games:
            await interaction.followup.send(
                f"{real_name}의 최근 랭크 전적이 없습니다."
            )
            return

        # ------------------------------
        # 현재 시즌
        # ------------------------------

        season_id = ranked_games[0].get(
            "seasonId"
        )

        current_season_games = [
            game
            for game in ranked_games
            if game.get("seasonId") == season_id
        ]

        # ------------------------------
        # 현재 랭크 정보
        # ------------------------------

        rank_data = await er_request(
            session,
            f"/v1/rank/uid/{uid}/{season_id}/3"
        )

        stats_data = await er_request(
            session,
            f"/v2/user/stats/uid/{uid}/{season_id}/3"
        )

        if not rank_data or not stats_data:
            await interaction.followup.send(
                "랭크 정보를 불러오지 못했습니다."
            )
            return

        user_rank = rank_data.get(
            "userRank"
        )

        stats_list = stats_data.get(
            "userStats",
            []
        )

        if not user_rank or not stats_list:
            await interaction.followup.send(
                "현재 시즌 랭크 정보가 없습니다."
            )
            return

        stats = stats_list[0]

        current_rp = user_rank.get(
            "mmr",
            stats.get("mmr", 0)
        )

        global_rank = user_rank.get(
            "rank",
            stats.get("rank", "-")
        )

        server_rank = user_rank.get(
            "serverRank"
        )

        total_games = stats.get(
            "totalGames",
            0
        )

        total_wins = stats.get(
            "totalWins",
            stats.get("wins", 0)
        )

        average_rank = stats.get(
            "averageRank",
            0
        )

        average_kills = stats.get(
            "averageKills",
            0
        )

        average_assists = stats.get(
            "averageAssistants",
            stats.get(
                "averageAssists",
                0
            )
        )

        # 승률
        if total_games > 0:
            win_rate = (
                total_wins
                / total_games
                * 100
            )
        else:
            win_rate = 0

        # ------------------------------
        # 캐릭터 이름
        # ------------------------------

        await load_character_names(
            session
        )

        # ------------------------------
        # 모스트 3
        # ------------------------------

        character_stats = stats.get(
            "characterStats",
            []
        )

        character_stats.sort(
            key=lambda x: x.get(
                "totalGames",
                0
            ),
            reverse=True
        )

        most_three = character_stats[:3]

        # ------------------------------
        # 최근 20게임
        # ------------------------------

        recent_ranked = (
            current_season_games[:20]
        )

        recent_ranks = [
            game.get(
                "gameRank",
                0
            )
            for game in recent_ranked
        ]

        if recent_ranks:
            recent_avg_rank = (
                sum(recent_ranks)
                / len(recent_ranks)
            )
        else:
            recent_avg_rank = 0

        # ------------------------------
        # RP 변화량
        # ------------------------------

        gains = []

        for game in recent_ranked:

            gain = game.get(
                "mmrGainGambit"
            )

            if gain is None:
                gain = game.get(
                    "mmrGain",
                    0
                )

            gains.append(gain)

        total_gain = sum(gains)

        # ------------------------------
        # RP 그래프용 점수
        # ------------------------------

        if (
            recent_ranked
            and all(
                game.get("mmrAfter")
                is not None
                for game in recent_ranked
            )
        ):

            scores = [
                game["mmrAfter"]
                for game
                in reversed(
                    recent_ranked
                )
            ]

        else:

            start_rp = (
                current_rp
                - sum(gains)
            )

            scores = [
                start_rp
            ]

            running = start_rp

            for gain in reversed(
                gains
            ):

                running += gain

                scores.append(
                    running
                )

        graph_buffer = make_rp_graph(
            scores, most_three
        )

        # ------------------------------
        # TOP3 경기 팀원 캐릭터
        # ------------------------------

        good_games = [
            game
            for game in current_season_games
            if game.get(
                "gameRank",
                99
            ) <= 3
        ][:8]

        teammate_counter = Counter()

        for game in good_games:

            game_id = game.get(
                "gameId"
            )

            my_team = game.get(
                "teamNumber"
            )

            if (
                game_id is None
                or my_team is None
            ):
                continue

            match_data = await er_request(
                session,
                f"/v1/games/{game_id}"
            )

            if not match_data:
                continue

            players = match_data.get(
                "userGames",
                []
            )

            for player in players:

                if (
                    player.get(
                        "teamNumber"
                    )
                    != my_team
                ):
                    continue

                player_id = (
                    player.get("userId")
                    or player.get("uid")
                )

                if player_id == uid:
                    continue

                character_code = player.get(
                    "characterNum"
                )

                if character_code is not None:

                    teammate_counter[
                        character_code
                    ] += 1

        teammate_top3 = (
            teammate_counter.most_common(
                3
            )
        )

    # ==================================================
    # Embed 생성
    # ==================================================

    embed = discord.Embed(
        title=real_name,
        description=(
            f"**{current_rp:,} RP**\n"
        )
    )

    # 현재 순위
    rank_text = ""

    if isinstance(
        global_rank,
        int
    ):
        rank_text += (
            f"전체 {global_rank:,}위"
        )
    else:
        rank_text += (
            f"전체 {global_rank}"
        )

    if server_rank is not None:
        rank_text += (
            f" | 서버 {server_rank:,}위"
        )

    embed.description += (
        f"{rank_text}\n\n"
        f"**{total_games}게임 | "
        f"승률 {win_rate:.1f}% | "
        f"평균 {average_rank:.1f}등 | "
        f"킬 {average_kills:.1f} | "
        f"어시 {average_assists:.1f}**\n\n"
        f"최근 {len(recent_ranked)}게임 | "
        f"평균 {recent_avg_rank:.1f}등 | "
        f"RP {total_gain:+d}"
    )

    # ------------------------------
    # 모스트 3
    # ------------------------------

    if most_three:

        for index, char in enumerate(
            most_three,
            start=1
        ):

            code = (
                char.get("characterCode")
                or char.get("characterNum")
            )

            character_name = (
                get_character_name(code)
            )

            games_played = char.get(
                "totalGames",
                0
            )

            wins = char.get(
                "wins",
                char.get(
                    "totalWins",
                    0
                )
            )

            if games_played > 0:
                char_win_rate = (
                    wins
                    / games_played
                    * 100
                )
            else:
                char_win_rate = 0

            avg_rank = char.get(
                "averageRank",
                0
            )


    # ------------------------------
    # 좋은 성적의 팀원 캐릭터
    # ------------------------------

    if teammate_top3:

        teammate_text = ""

        for index, (
            code,
            count
        ) in enumerate(
            teammate_top3,
            start=1
        ):

            name = get_character_name(
                code
            )

            teammate_text += (
                f"{index}. {name} · "
                f"{count}회\n"
            )

        embed.add_field(
            name="TOP3 경기에서 자주 함께한 캐릭터",
            value=teammate_text,
            inline=False
        )

    # ==================================================
    # DAK.GG 버튼
    # ==================================================

    view = discord.ui.View()

    dak_url = (
        "https://dak.gg/er/players/"
        + quote(real_name)
    )

    dak_button = discord.ui.Button(
        label="DAK.GG",
        url=dak_url
    )

    view.add_item(
        dak_button
    )

    # ==================================================
    # 그래프 출력
    # ==================================================

    if graph_buffer:

        file = discord.File(
            graph_buffer,
            filename="rp_trend.png"
        )

        embed.set_image(
            url="attachment://rp_trend.png"
        )

        await interaction.followup.send(
            embed=embed,
            file=file,
            view=view
        )

    else:

        await interaction.followup.send(
            embed=embed,
            view=view
        )

        # ------------------------------
        # 최근 게임
        # ------------------------------

        games_data = await er_request(
            session,
            f"/v1/user/games/uid/{uid}"
        )

        if not games_data:

            await interaction.followup.send(
                "전적 정보를 불러오지 못했습니다."
            )

            return

        games = games_data.get(
            "userGames",
            []
        )

        ranked_games = [
            game
            for game in games
            if game.get("matchingMode") == 3
        ]

        if not ranked_games:

            await interaction.followup.send(
                f"{real_name}의 최근 랭크 전적이 없습니다."
            )

            return

        # 가장 최근 랭크 경기의 시즌을 현재 시즌으로 사용
        season_id = ranked_games[0].get(
            "seasonId"
        )

        current_season_games = [
            game
            for game in ranked_games
            if game.get("seasonId")
            == season_id
        ]

        # ------------------------------
        # 현재 랭크
        # ------------------------------

        rank_data = await er_request(
            session,
            f"/v1/rank/uid/{uid}/{season_id}/3"
        )

        # ------------------------------
        # 시즌 통계
        # ------------------------------

        stats_data = await er_request(
            session,
            f"/v2/user/stats/uid/{uid}/{season_id}/3"
        )

        if not rank_data or not stats_data:

            await interaction.followup.send(
                "랭크 정보를 불러오지 못했습니다."
            )

            return

        user_rank = rank_data.get(
            "userRank"
        )

        stats_list = stats_data.get(
            "userStats",
            []
        )

        if (
            not user_rank
            or not stats_list
        ):

            await interaction.followup.send(
                "현재 시즌 랭크 정보가 없습니다."
            )

            return

        stats = stats_list[0]

        current_rp = user_rank.get(
            "mmr",
            stats.get(
                "mmr",
                0
            )
        )

        global_rank = user_rank.get(
            "rank",
            stats.get(
                "rank",
                "-"
            )
        )

        server_rank = user_rank.get(
            "serverRank"
        )

        total_games = stats.get(
            "totalGames",
            0
        )

        average_rank = stats.get(
            "averageRank",
            0
        )

        average_kills = stats.get(
            "averageKills",
            0
        )

        # ------------------------------
        # 캐릭터 이름 데이터
        # ------------------------------

        await load_character_names(
            session
        )

        # ------------------------------
        # 모스트 3
        # ------------------------------

        character_stats = stats.get(
            "characterStats",
            []
        )

        character_stats.sort(
            key=lambda x: x.get(
                "totalGames",
                0
            ),
            reverse=True
        )

        most_three = (
            character_stats[:3]
        )

        # ------------------------------
        # 좋은 경기의 팀원 캐릭터
        # ------------------------------

        good_games = [
            game
            for game in current_season_games
            if game.get(
                "gameRank",
                99
            ) <= 3
        ][:8]

        teammate_counter = Counter()

        for game in good_games:

            game_id = game.get(
                "gameId"
            )

            my_team = game.get(
                "teamNumber"
            )

            if (
                game_id is None
                or my_team is None
            ):
                continue

            match_data = await er_request(
                session,
                f"/v1/games/{game_id}"
            )

            if not match_data:
                continue

            players = match_data.get(
                "userGames",
                []
            )

            for player in players:

                if (
                    player.get(
                        "teamNumber"
                    )
                    != my_team
                ):
                    continue

                # 자기 자신 제외
                if (
                    player.get(
                        "nickname"
                    )
                    == real_name
                ):
                    continue

                code = player.get(
                    "characterNum"
                )

                if code is not None:

                    teammate_counter[
                        code
                    ] += 1

        teammate_top3 = (
            teammate_counter.most_common(
                3
            )
        )

    # ==================================================
    # RP 그래프 계산
    # ==================================================

    recent_ranked = (
        current_season_games[:10]
    )

    # API가 mmrAfter를 제공하는 경우
    if (
        recent_ranked
        and all(
            game.get(
                "mmrAfter"
            )
            is not None
            for game in recent_ranked
        )
    ):

        scores = [
            game["mmrAfter"]
            for game in reversed(
                recent_ranked
            )
        ]

        graph_note = (
            "최근 랭크 경기 기준"
        )

    else:

        # mmrAfter가 없으면
        # 현재 RP와 mmrGain으로 역산

        gains = []

        for game in recent_ranked:

            gain = game.get(
                "mmrGainGambit"
            )

            if gain is None:
                gain = game.get(
                    "mmrGain",
                    0
                )

            gains.append(
                gain
            )

        start_rp = (
            current_rp
            - sum(gains)
        )

        scores = [
            start_rp
        ]

        running = start_rp

        for gain in reversed(
            gains
        ):

            running += gain

            scores.append(
                running
            )

        graph_note = (
            "최근 랭크 경기 RP 변화"
        )

    graph_buffer = make_rp_graph(
        scores
    )

    # ==================================================
    # 메시지 작성
    # ==================================================

    message = (
        f"# {real_name}\n"
        f"랭크 정보\n"
        f"────────────────\n\n"
        f"**현재 RP**\n"
        f"{current_rp:,}\n\n"
        f"**전체 순위**\n"
        f"{global_rank:,}위"
        if isinstance(
            global_rank,
            int
        )
        else
        f"# {real_name}\n"
        f"랭크 정보\n"
        f"────────────────\n\n"
        f"**현재 RP**\n"
        f"{current_rp:,}\n\n"
        f"**전체 순위**\n"
        f"{global_rank}"
    )

    if server_rank is not None:

        message += (
            f"\n\n**서버 순위**\n"
            f"{server_rank:,}위"
        )

    message += (
        f"\n\n**시즌 기록**\n"
        f"{total_games}게임 · "
        f"평균 {average_rank:.2f}위 · "
        f"평균 {average_kills:.2f}킬\n"
    )

    # ------------------------------
    # 모스트 3 출력
    # ------------------------------

    message += (
        "\n**모스트 3**\n"
    )

    if most_three:

        for index, char in enumerate(
            most_three,
            start=1
        ):

            code = char.get(
                "characterCode"
            )

            name = get_character_name(
                code
            )

            games_played = char.get(
                "totalGames",
                0
            )

            wins = char.get(
                "wins",
                0
            )

            average = char.get(
                "averageRank",
                0
            )

            message += (
                f"{index}. {name} · "
                f"{games_played}게임"
            )

            if games_played > 0:

                win_rate = (
                    wins
                    / games_played
                    * 100
                )

                message += (
                    f" · 승률 {win_rate:.1f}%"
                )

            message += (
                f" · 평균 {average:.2f}위\n"
            )

    else:

        message += (
            "기록 없음\n"
        )

    # ------------------------------
    # 좋은 성적 팀원
    # ------------------------------

    message += (
        "\n**TOP3 경기에서 자주 함께한 캐릭터**\n"
    )

    if teammate_top3:

        for index, (
            code,
            count
        ) in enumerate(
            teammate_top3,
            start=1
        ):

            name = get_character_name(
                code
            )

            message += (
                f"{index}. {name} · "
                f"{count}회\n"
            )

    else:

        message += (
            "분석할 기록이 없습니다.\n"
        )

    message += (
        f"\n{graph_note}"
    )

    # ==================================================
    # 결과 전송
    # ==================================================

    if graph_buffer:

        file = discord.File(
            graph_buffer,
            filename="rp_trend.png"
        )

        await interaction.followup.send(
            message,
            file=file
        )

    else:

        await interaction.followup.send(
            message
        )

# ==================================================
# 실행
# ==================================================

if not TOKEN:

    print(
        ".env에서 DISCORD_TOKEN을 찾지 못했습니다."
    )

else:

    bot.run(TOKEN)