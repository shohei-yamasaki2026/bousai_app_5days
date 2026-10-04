from flask import Flask, jsonify, request, render_template, session, redirect, url_for
from urllib.parse import urlparse, urljoin
from functools import wraps
import hmac
import json
import os
import re
import secrets
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

# app.py はプロジェクト直下に置く。
# 実体（templates / static / data）は bousai_app/ 配下にあるので、そこを参照する。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(BASE_DIR, 'bousai_app')

app = Flask(
    __name__,
    template_folder=os.path.join(APP_DIR, 'templates'),
    static_folder=os.path.join(APP_DIR, 'static'),
)
app.secret_key = os.environ.get('FLASK_SECRET_KEY')
if not app.secret_key:
    app.logger.warning("FLASK_SECRET_KEY is unset; using an ephemeral development key")
    app.secret_key = secrets.token_hex(32)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# 管理者認証情報
ADMIN_CREDENTIALS = {
    'admin': '123'
}

# ────────────────────────────────
# 気象警報・注意報設定
PREFECTURE_CODE = "020000"  # 青森県
AREA_NAME = "青森市"

# 気象庁の警報・注意報データにおける青森市の市区町村コード
AREA_CODE = "0220100"

# shelters.json stores the selected option keys in these list fields.
DISASTER_TYPE_OPTIONS = (
    ('earthquake', '地震'),
    ('tsunami', '津波'),
    ('landslide', '土砂災害'),
    ('flood', '洪水'),
    ('fire', '火災'),
    ('storm_snow', '暴風・大雪'),
)
FACILITY_OPTIONS = (
    ('pets_allowed', 'ペット同伴'),
    ('barrier_free', 'バリアフリー'),
    ('emergency_power', '非常用電源'),
    ('wifi', 'Wi-Fi'),
    ('nursing_room', '授乳室'),
    ('multipurpose_toilet', '多目的トイレ'),
)
SHELTER_DISASTER_OPTIONS = (
    ('earthquake', '地震'),
    ('tsunami', '津波'),
    ('flood', '洪水'),
    ('landslide', '土砂災害'),
    ('fire', '火災'),
    ('storm_snow', '暴風・大雪'),
)
SHELTER_FACILITY_OPTIONS = (
    ('pets_allowed', 'ペット可'),
    ('barrier_free', 'バリアフリー'),
    ('emergency_power', '非常用電源'),
    ('wifi', 'Wi-Fi'),
    ('nursing_room', '授乳室'),
    ('multipurpose_toilet', '多目的トイレ'),
)
INSTRUCTION_PRIORITIES = ('高', '中', '低')
INSTRUCTION_STATUSES = ('未対応', '対応中', '完了')

WARNING_URL = (
    f"https://www.jma.go.jp/bosai/warning/data/r8/{PREFECTURE_CODE}.json"
)
weather_warnings_cache = None

JST = timezone(timedelta(hours=9))

# 警報・注意報のコード一覧
WARNING_CODES = {
    "00": "解除",
    "02": "暴風雪警報",
    "03": "レベル3大雨警報",
    "04": "洪水警報",
    "05": "暴風警報",
    "06": "大雪警報",
    "07": "波浪警報",
    "08": "レベル3高潮警報",
    "09": "レベル3土砂災害警報",
    "10": "レベル2大雨注意報",
    "12": "大雪注意報",
    "13": "風雪注意報",
    "14": "雷注意報",
    "15": "強風注意報",
    "16": "波浪注意報",
    "17": "融雪注意報",
    "18": "洪水注意報",
    "19": "レベル2高潮注意報",
    "20": "濃霧注意報",
    "21": "乾燥注意報",
    "22": "なだれ注意報",
    "23": "低温注意報",
    "24": "霜注意報",
    "25": "着氷注意報",
    "26": "着雪注意報",
    "27": "その他の注意報",
    "29": "レベル2土砂災害注意報",
    "32": "暴風雪特別警報",
    "33": "レベル5大雨特別警報",
    "35": "暴風特別警報",
    "36": "大雪特別警報",
    "37": "波浪特別警報",
    "38": "レベル5高潮特別警報",
    "39": "レベル5土砂災害特別警報",
    "43": "レベル4大雨危険警報",
    "48": "レベル4高潮危険警報",
    "49": "レベル4土砂災害危険警報"
}

# ────────────────────────────────
# サンプルデータの読み込み
DATA_FILE = os.path.join(APP_DIR, 'data', 'shelters.json')
INSTRUCTIONS_FILE = os.path.join(APP_DIR, 'data', 'instructions.json')

def load_json(path, default):
    """JSONファイルを読み込む（存在しない・壊れている場合は default を返す）"""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

def load_shelters():
    """避難所データを読み込み、画面に通知できるよう失敗状態も返す"""
    try:
        with open(DATA_FILE, encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError("避難所データが配列ではありません")
        return data, False
    except (OSError, json.JSONDecodeError, ValueError):
        app.logger.exception("避難所データの読み込みに失敗しました")
        return [], True

def save_shelters():
    """避難所データをファイルに保存する"""
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w',
            encoding='utf-8',
            dir=os.path.dirname(DATA_FILE),
            delete=False
        ) as f:
            temporary_path = f.name
            json.dump(shelters, f, ensure_ascii=False, indent=2)
        os.replace(temporary_path, DATA_FILE)
    except OSError:
        app.logger.exception("避難所データの保存に失敗しました")
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)
        raise

shelters, shelter_data_error = load_shelters()
instructions = load_json(INSTRUCTIONS_FILE, [])
if not isinstance(instructions, list):
    app.logger.error("指示データが配列ではありません")
    instructions = []

def save_instructions():
    """指示ボードのデータをファイルに保存する"""
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w',
            encoding='utf-8',
            dir=os.path.dirname(INSTRUCTIONS_FILE),
            delete=False
        ) as f:
            temporary_path = f.name
            json.dump(instructions, f, ensure_ascii=False, indent=2)
        os.replace(temporary_path, INSTRUCTIONS_FILE)
    except OSError:
        app.logger.exception("指示データの保存に失敗しました")
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)
        raise
# ────────────────────────────────

# ────────────────────────────────
# 認証関連の設定とヘルパー関数
def is_safe_url(target):
    """リダイレクト先URLが安全かどうかチェック"""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

def login_required(f):
    """認証が必要なページに付けるデコレータ"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            # 現在のURLをnextパラメータとしてログイン画面にリダイレクト
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def get_japan_time():
    """日本時間（JST）の現在時刻を取得する"""
    return datetime.now(JST).strftime("%Y年%m月%d日 %H:%M")

def get_japan_datetime():
    return datetime.now(JST)

def format_datetime(value):
    if isinstance(value, datetime):
        return value.astimezone(JST).strftime("%Y年%m月%d日 %H:%M")
    return get_japan_time()

def get_csrf_token():
    token = session.get('_csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['_csrf_token'] = token
    return token

def valid_csrf_token(candidate):
    expected = session.get('_csrf_token', '')
    return bool(expected and candidate and hmac.compare_digest(expected, candidate))

def instruction_is_active(instruction):
    return (
        isinstance(instruction, dict)
        and instruction.get('target') == '住民'
        and instruction.get('status') in ('有効', '発信中', 'active', 'published')
    )

def instruction_sort_key(instruction):
    timestamp = instruction.get('created_at_iso', '')
    try:
        parsed = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError):
        try:
            parsed = datetime.strptime(instruction.get('created_at', ''), '%Y年%m月%d日 %H:%M')
        except (TypeError, ValueError):
            return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=JST)
    return parsed.timestamp()

def get_disaster_information():
    try:
        with open(os.path.join(APP_DIR, 'data', 'notification_history.json'), encoding='utf-8') as f:
            records = json.load(f)
    except (OSError, json.JSONDecodeError):
        app.logger.exception("災害情報履歴の読み込みに失敗しました")
        return [], True
    if not isinstance(records, list):
        app.logger.error("災害情報履歴が配列ではありません")
        return [], True
    valid_records = []
    for item in records:
        if not isinstance(item, dict):
            continue
        category = (
            'emergency' if item.get('has_emergency')
            else 'warning' if item.get('has_warning')
            else 'information'
        )
        valid_records.append({**item, 'category': category})
    return sorted(valid_records, key=disaster_sort_key, reverse=True), False

def disaster_sort_key(item):
    timestamp = item.get('timestamp', '')
    for date_format in ('%Y年%m月%d日 %H:%M', '%Y-%m-%dT%H:%M:%S%z'):
        try:
            parsed = datetime.strptime(timestamp, date_format)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=JST)
            return parsed.timestamp()
        except (TypeError, ValueError):
            continue
    return 0

def active_resident_instructions():
    return sorted(
        (item for item in instructions if instruction_is_active(item)),
        key=instruction_sort_key,
        reverse=True
    )

def resident_home_announcements():
    return sorted(
        (
            item for item in instructions
            if isinstance(item, dict)
            and item.get('kind') == 'announcement'
            and item.get('target') == '住民'
            and item.get('display_on_home') is True
        ),
        key=lambda item: instruction_sort_key({
            'created_at_iso': item.get('sent_at_iso'),
            'created_at': item.get('sent_at')
        }),
        reverse=True
    )

def instruction_priority_sort_key(instruction):
    priority_order = {'高': 0, '緊急': 0, '中': 1, '通常': 1, '低': 2}
    priority = instruction.get('priority', instruction.get('urgency'))
    return (priority_order.get(priority, 3), -instruction_sort_key(instruction))

def normalize_instruction_status(status):
    return {
        '有効': '未対応',
        '発信中': '対応中',
        'active': '未対応',
        'published': '未対応',
        '解除': '完了',
        '終了': '完了'
    }.get(status, status if status in INSTRUCTION_STATUSES else '未対応')

def get_map_shelters():
    result = []
    for shelter in shelters:
        if not isinstance(shelter, dict):
            continue
        if isinstance(shelter.get('latitude'), bool) or isinstance(shelter.get('longitude'), bool):
            continue
        try:
            latitude = float(shelter['latitude'])
            longitude = float(shelter['longitude'])
        except (KeyError, TypeError, ValueError):
            continue
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            continue
        result.append({
            'id': shelter.get('id'),
            'name': str(shelter.get('name') or '名称未登録'),
            'latitude': latitude,
            'longitude': longitude,
            'opening_status': str(shelter.get('opening_status') or '未登録')
        })
    return result

def format_report_time(iso_str):
    """気象庁の発表時刻（ISO形式）をJSTの表示用文字列に変換する"""
    if not iso_str:
        return "不明"
    try:
        parsed = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        if parsed.tzinfo:
            parsed = parsed.astimezone(JST)
        return parsed.strftime("%Y年%m月%d日 %H:%M")
    except ValueError:
        return iso_str


def filter_shelters(district=None):
    """district 指定があれば一致する避難所のみ、なければ全件を返す"""
    return [s for s in shelters if not district or s.get('district') == district]

def search_shelters(keyword='', district='', disaster_types=(), facilities=()):
    """キーワード、地区、登録済みの災害対応・設備条件で避難所を検索する"""
    normalized_keyword = keyword.strip().casefold()
    return [
        shelter for shelter in shelters
        if isinstance(shelter, dict)
        and (not district or shelter.get('district') == district)
        and (
            not disaster_types
            or (
                isinstance(shelter.get('disaster_types'), list)
                and all(
                    disaster_type in shelter['disaster_types']
                    for disaster_type in disaster_types
                )
            )
        )
        and (
            not facilities
            or (
                isinstance(shelter.get('facilities'), list)
                and all(
                    facility in shelter['facilities']
                    for facility in facilities
                )
            )
        )
        and (
            not normalized_keyword
            or normalized_keyword in str(shelter.get('name', '')).casefold()
            or normalized_keyword in str(shelter.get('district', '')).casefold()
        )
    ]

def get_shelter_districts():
    """実データに登録されている地区名のみを選択肢として返す"""
    return sorted({
        shelter['district']
        for shelter in shelters
        if isinstance(shelter, dict) and isinstance(shelter.get('district'), str)
        and shelter['district'].strip()
    })

def get_shelter_return_url(candidate):
    """検索結果一覧など、許可した同一サイト内の戻り先だけを受け付ける"""
    parsed = urlparse(candidate or '')
    if not parsed.netloc and parsed.path in ('/search_results', '/all_shelters'):
        return candidate
    return url_for('shelter_search')


def parse_area_warnings(warning_data):
    """気象庁の新形式JSONから対象市区町村の発表・継続中の情報を抽出する"""
    if not isinstance(warning_data, list):
        raise ValueError("気象庁の警報・注意報データが新形式の配列ではありません")

    warnings = []
    seen_codes = set()
    report_datetimes = []

    for report in warning_data:
        if not isinstance(report, dict):
            continue

        warning = report.get("warning")
        if not isinstance(warning, dict):
            continue

        class20_items = warning.get("class20Items", [])
        if not isinstance(class20_items, list):
            continue

        area = next(
            (
                item for item in class20_items
                if isinstance(item, dict)
                and item.get("areaCode") == AREA_CODE
            ),
            None
        )
        if not area:
            continue

        report_datetime = report.get("reportDatetime")
        if isinstance(report_datetime, str) and report_datetime:
            report_datetimes.append(report_datetime)

        kinds = area.get("kinds", [])
        if not isinstance(kinds, list):
            continue

        for kind in kinds:
            if not isinstance(kind, dict):
                continue

            status = kind.get("status", "")
            code = kind.get("code", "")
            if status not in ("発表", "継続") or not code or code in seen_codes:
                continue

            warnings.append({
                "name": WARNING_CODES.get(
                    code,
                    f"不明な警報・注意報 (コード: {code})"
                ),
                "code": code,
                "status": status
            })
            seen_codes.add(code)

    latest_report_datetime = max(report_datetimes, default="")
    return warnings, latest_report_datetime


def get_weather_warnings():
    """対象市区町村の警報・注意報を取得する"""
    global weather_warnings_cache
    try:
        # 青森県の新形式（令和8年～）警報・注意報データを取得
        with urllib.request.urlopen(url=WARNING_URL, timeout=10) as res:
            warning_data = json.loads(res.read())

        warnings, report_datetime = parse_area_warnings(warning_data)

        weather_warnings_cache = {
            "area_name": AREA_NAME,
            "warnings": warnings,
            "report_time": format_report_time(report_datetime),
            "last_fetch_time": get_japan_time(),
            "error": False,
            "stale": False
        }
        return weather_warnings_cache

    except (OSError, ValueError, urllib.error.URLError):
        app.logger.exception("気象情報の取得に失敗しました")
        if weather_warnings_cache:
            return {
                **weather_warnings_cache,
                "error": True,
                "stale": True,
                "last_attempt_time": get_japan_time()
            }
        return {
            "area_name": AREA_NAME,
            "warnings": [],
            "report_time": "取得失敗",
            "last_fetch_time": get_japan_time(),
            "error": True,
            "stale": False
        }


# トップページ：templates/index.html を返す（住民向け指示も表示する）
@app.route('/')
def index():
    disaster_information, disaster_data_error = get_disaster_information()
    return render_template(
        'index.html',
        resident_notices=active_resident_instructions()[:5],
        resident_announcements=resident_home_announcements()[:5],
        disaster_information=disaster_information[:3],
        disaster_data_error=disaster_data_error,
        map_shelters=get_map_shelters(),
        map_data_error=shelter_data_error
    )

@app.route('/disaster_information')
def disaster_information():
    selected_category = request.args.get('category', '')
    valid_categories = {'emergency', 'warning', 'information'}
    if selected_category not in valid_categories:
        selected_category = ''
    records, data_error = get_disaster_information()
    if selected_category:
        records = [item for item in records if item['category'] == selected_category]
    return render_template(
        'disaster_information.html',
        records=records,
        data_error=data_error,
        selected_category=selected_category,
        category_labels={
            'emergency': '緊急',
            'warning': '警報',
            'information': '情報'
        }
    )

# ログインページ
@app.route('/login', methods=['GET', 'POST'])
def login():
    # リダイレクト先を取得（デフォルトは避難所登録画面）
    next_url = request.args.get('next') or request.form.get('next')

    # 安全でないURLの場合はデフォルトページにリダイレクト
    if not next_url or not is_safe_url(next_url):
        next_url = url_for('shelter_register')

    if request.method == 'POST':
        password = request.form.get('password', '').strip()

        # 認証チェック
        username = next(
            (name for name, registered_password in ADMIN_CREDENTIALS.items()
             if registered_password == password),
            None
        )
        if username:
            session['logged_in'] = True
            session['username'] = username
            # ログイン成功後は指定されたページにリダイレクト
            return redirect(next_url)
        return render_template('login.html', error=True, message="パスワードが正しくありません。", next=next_url)

    # ログイン済みの場合は指定されたページにリダイレクト
    if session.get('logged_in'):
        return redirect(next_url)

    return render_template('login.html', next=next_url)

# ログアウト
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# 避難所登録・管理ページ
@app.route('/shelter_register', methods=['GET', 'POST'])
@login_required
def shelter_register():
    edit_id = request.args.get('edit', type=int)
    editing_shelter = None
    if edit_id is not None:
        editing_shelter = next(
            (
                item for item in shelters
                if isinstance(item, dict)
                and item.get('id') == edit_id
                and not isinstance(item.get('id'), bool)
            ),
            None
        )
        if editing_shelter is None:
            return render_template(
                'shelter_register.html',
                error=True,
                message='編集する避難所が見つかりません。',
                form_data={},
                shelters=shelters,
                disaster_options=SHELTER_DISASTER_OPTIONS,
                facility_options=SHELTER_FACILITY_OPTIONS,
                csrf_token=get_csrf_token(),
                editing_id=None
            ), 404

    form_data = {
        'name': editing_shelter.get('name', '') if editing_shelter else '',
        'address': editing_shelter.get('address', '') if editing_shelter else '',
        'district': editing_shelter.get('district', '') if editing_shelter else '',
        'capacity': str(editing_shelter.get('capacity', '')) if editing_shelter else '',
        'disasters': (
            editing_shelter.get('disasters', editing_shelter.get('disaster_types', []))
            if editing_shelter else []
        ),
        'facilities': editing_shelter.get('facilities', []) if editing_shelter else [],
        'status': (
            editing_shelter.get('status', editing_shelter.get('opening_status', '未開設'))
            if editing_shelter else '未開設'
        )
    }
    if not isinstance(form_data['disasters'], list):
        form_data['disasters'] = []
    if not isinstance(form_data['facilities'], list):
        form_data['facilities'] = []
    error = False
    server_error = False
    message = ''
    success_messages = {
        'created': '避難所を登録しました。',
        'updated': '避難所情報を更新しました。'
    }
    success = request.args.get('success') in success_messages
    message = success_messages.get(request.args.get('success', ''), '')
    if request.method == 'POST':
        form_data = {
            'name': request.form.get('name', '').strip(),
            'address': request.form.get('address', '').strip(),
            'district': request.form.get('district', '').strip(),
            'capacity': request.form.get('capacity', '').strip(),
            'disasters': request.form.getlist('disasters'),
            'facilities': request.form.getlist('facilities'),
            'status': request.form.get('status', '')
        }
        submitted_edit_id = request.form.get('edit_id', '').strip()
        if not valid_csrf_token(request.form.get('csrf_token', '')):
            error, message = True, '画面の有効期限が切れました。再読み込みしてもう一度お試しください。'
        elif (edit_id is not None and submitted_edit_id != str(edit_id)) or (submitted_edit_id and (
            not submitted_edit_id.isascii()
            or not submitted_edit_id.isdigit()
            or len(submitted_edit_id) > 12
            or not any(
                isinstance(item, dict) and item.get('id') == int(submitted_edit_id)
                for item in shelters
            )
        )):
            error, message = True, '編集する避難所が見つかりません。'
        elif not form_data['name']:
            error, message = True, '避難所名を入力してください。'
        elif len(form_data['name']) > 120:
            error, message = True, '避難所名は120文字以内で入力してください。'
        elif not form_data['address']:
            error, message = True, '住所を入力してください。'
        elif len(form_data['address']) > 250:
            error, message = True, '住所は250文字以内で入力してください。'
        elif not re.fullmatch(r'[0-9]+', form_data['capacity']):
            error, message = True, '収容人数は半角数字で入力してください。'
        elif len(form_data['capacity']) > 9:
            error, message = True, '収容人数は9桁以内で入力してください。'
        elif int(form_data['capacity']) <= 0:
            error, message = True, '収容人数は1以上で入力してください。'
        elif len(form_data['district']) > 80:
            error, message = True, '地域名は80文字以内で入力してください。'
        elif any(value not in dict(SHELTER_DISASTER_OPTIONS) for value in form_data['disasters']):
            error, message = True, '対応可能な災害種別に不正な値が含まれています。'
        elif any(value not in dict(SHELTER_FACILITY_OPTIONS) for value in form_data['facilities']):
            error, message = True, '避難所設備に不正な値が含まれています。'
        elif form_data['status'] not in ('未開設', '開設中', '閉鎖中'):
            error, message = True, '開設状況を選択してください。'
        else:
            existing_ids = [
                item.get('id') for item in shelters
                if isinstance(item, dict)
                and isinstance(item.get('id'), int)
                and not isinstance(item.get('id'), bool)
            ]
            is_update = edit_id is not None
            target_id = edit_id if is_update else max(existing_ids, default=0) + 1
            previous_record = next(
                (item for item in shelters if isinstance(item, dict) and item.get('id') == target_id),
                None
            )
            updated_record = dict(previous_record or {})
            updated_record.update({
                'id': target_id,
                'name': form_data['name'],
                'address': form_data['address'],
                'district': form_data['district'],
                'capacity': int(form_data['capacity']),
                'disasters': list(dict.fromkeys(form_data['disasters'])),
                'disaster_types': list(dict.fromkeys(form_data['disasters'])),
                'facilities': list(dict.fromkeys(form_data['facilities'])),
                'status': form_data['status'],
                'opening_status': form_data['status']
            })
            if previous_record is None:
                shelters.append(updated_record)
            else:
                shelters[shelters.index(previous_record)] = updated_record
            try:
                save_shelters()
                return redirect(url_for(
                    'shelter_register',
                    success='updated' if is_update else 'created'
                ))
            except OSError:
                if previous_record is None:
                    shelters.remove(updated_record)
                else:
                    shelters[shelters.index(updated_record)] = previous_record
                error = True
                server_error = True
                message = '避難所情報を保存できませんでした。時間をおいて再度お試しください。'
                edit_id = target_id if is_update else None
                editing_shelter = previous_record

    return render_template(
        'shelter_register.html',
        error=error,
        success=success,
        message=message,
        form_data=form_data,
        shelters=[item for item in shelters if isinstance(item, dict)],
        disaster_options=SHELTER_DISASTER_OPTIONS,
        facility_options=SHELTER_FACILITY_OPTIONS,
        csrf_token=get_csrf_token(),
        editing_id=edit_id
    ), 500 if server_error else 400 if error else 200

# 避難所検索ページ
@app.route('/shelter_search')
def shelter_search():
    selected_disaster_types = [
        value for value in request.args.getlist('disaster_types')
        if value in dict(DISASTER_TYPE_OPTIONS)
    ]
    selected_facilities = [
        value for value in request.args.getlist('facilities')
        if value in dict(FACILITY_OPTIONS)
    ]
    return render_template(
        'shelter_search.html',
        districts=get_shelter_districts(),
        data_error=shelter_data_error,
        keyword=request.args.get('keyword', ''),
        selected_district=request.args.get('district', ''),
        disaster_type_options=DISASTER_TYPE_OPTIONS,
        facility_options=FACILITY_OPTIONS,
        selected_disaster_types=selected_disaster_types,
        selected_facilities=selected_facilities
    )

# 全施設一覧ページ
@app.route('/all_shelters')
def all_shelters():
    return render_template(
        'search_results.html',
        results=shelters,
        keyword='',
        district='',
        selected_conditions=[],
        search_url=url_for('shelter_search'),
        data_error=shelter_data_error
    )

@app.route('/shelters/<int:shelter_id>')
def shelter_detail(shelter_id):
    shelter = next(
        (
            item for item in shelters
            if isinstance(item, dict)
            and isinstance(item.get('id'), int)
            and not isinstance(item.get('id'), bool)
            and item['id'] == shelter_id
        ),
        None
    )
    return_url = get_shelter_return_url(request.args.get('return_to'))
    if shelter is None:
        return render_template(
            'shelter_detail.html',
            shelter=None,
            return_url=return_url,
            disaster_type_labels=dict(DISASTER_TYPE_OPTIONS),
            facility_labels=dict(FACILITY_OPTIONS)
        ), 404

    return render_template(
        'shelter_detail.html',
        shelter=shelter,
        return_url=return_url,
        disaster_type_labels=dict(DISASTER_TYPE_OPTIONS),
        facility_labels=dict(FACILITY_OPTIONS)
    )


# 指示ボード：住民向けの指示を一覧で確認する
@app.route('/board', methods=['GET', 'POST'])
@login_required
def board():
    error = None
    form_data = {}
    sort_order = request.args.get('sort', 'recent')
    if sort_order not in ('recent', 'priority'):
        sort_order = 'recent'
    success_messages = {
        'instruction': '指示を登録しました。',
        'announcement': '住民向け発信を登録しました。',
        'status': '対応状況を更新しました。'
    }
    success_message = success_messages.get(request.args.get('success', ''))
    if request.method == 'POST':
        if not valid_csrf_token(request.form.get('csrf_token', '')):
            error = '画面の有効期限が切れました。再読み込みしてもう一度お試しください。'
        else:
            action = request.form.get('action', '')
            if action == 'create_instruction':
                instruction_form = {
                    'target': request.form.get('target', ''),
                    'target_area': request.form.get('target_area', '').strip(),
                    'recipient': request.form.get('recipient', ''),
                    'content': request.form.get('content', '').strip(),
                    'shelter': request.form.get('shelter', '').strip(),
                    'priority': request.form.get('priority', '')
                }
                form_data = {'instruction': instruction_form}
                if instruction_form['target'] not in ('住民', '防災担当部署', '道路管理担当部署', 'その他'):
                    error = '対象を選択してください。'
                elif instruction_form['recipient'] not in ('住民向け', '担当部署', '防災担当部署', '道路管理担当部署'):
                    error = '宛先を選択してください。'
                elif not instruction_form['content'] or len(instruction_form['content']) > 2000:
                    error = '指示内容は1〜2000文字で入力してください。'
                elif instruction_form['priority'] not in INSTRUCTION_PRIORITIES:
                    error = '緊急度を選択してください。'
                elif len(instruction_form['target_area']) > 100 or len(instruction_form['shelter']) > 120:
                    error = '対象地区は100文字以内、避難先は120文字以内で入力してください。'
                else:
                    ids = [
                        item.get('id') for item in instructions
                        if isinstance(item, dict)
                        and isinstance(item.get('id'), int)
                        and not isinstance(item.get('id'), bool)
                    ]
                    now = get_japan_datetime()
                    record = {
                        'id': max(ids, default=0) + 1,
                        'kind': 'instruction',
                        **instruction_form,
                        'status': '未対応',
                        'created_at': format_datetime(now),
                        'created_at_iso': now.isoformat(),
                        'updated_at': format_datetime(now)
                    }
                    instructions.append(record)
                    try:
                        save_instructions()
                        return redirect(url_for('board', sort=sort_order, success='instruction'))
                    except OSError:
                        instructions.remove(record)
                        error = '指示を保存できませんでした。時間をおいて再度お試しください。'
            elif action == 'create_announcement':
                announcement = {
                    'target_area': request.form.get('target_area', '').strip(),
                    'recipient': request.form.get('recipient', '').strip(),
                    'content': request.form.get('content', '').strip(),
                    'display_on_home': request.form.get('display_on_home') == 'on'
                }
                form_data = {'announcement': announcement}
                if not announcement['recipient'] or len(announcement['recipient']) > 120:
                    error = '発信先を1〜120文字で入力してください。'
                elif len(announcement['target_area']) > 100:
                    error = '対象地区は100文字以内で入力してください。'
                elif not announcement['content'] or len(announcement['content']) > 2000:
                    error = '発信内容は1〜2000文字で入力してください。'
                else:
                    ids = [
                        item.get('id') for item in instructions
                        if isinstance(item, dict)
                        and isinstance(item.get('id'), int)
                        and not isinstance(item.get('id'), bool)
                    ]
                    now = get_japan_datetime()
                    record = {
                        'id': max(ids, default=0) + 1,
                        'kind': 'announcement',
                        'target': '住民',
                        **announcement,
                        'sent_at': format_datetime(now),
                        'sent_at_iso': now.isoformat()
                    }
                    instructions.append(record)
                    try:
                        save_instructions()
                        return redirect(url_for('board', sort=sort_order, success='announcement'))
                    except OSError:
                        instructions.remove(record)
                        error = '発信内容を保存できませんでした。時間をおいて再度お試しください。'
            elif action == 'update_instruction_status':
                instruction_id = request.form.get('instruction_id', '')
                status = request.form.get('status', '')
                selected = next(
                    (
                        item for item in instructions
                        if isinstance(item, dict)
                        and item.get('id') == int(instruction_id)
                    ),
                    None
                ) if instruction_id.isascii() and instruction_id.isdigit() else None
                if status not in INSTRUCTION_STATUSES:
                    error = '対応状況を選択してください。'
                elif selected is None or selected.get('kind') == 'announcement':
                    error = '更新する指示が見つかりません。'
                else:
                    previous_status = selected.get('status')
                    previous_updated = selected.get('updated_at')
                    selected['status'] = status
                    selected['updated_at'] = get_japan_time()
                    try:
                        save_instructions()
                        return redirect(url_for('board', sort=sort_order, success='status'))
                    except OSError:
                        selected['status'] = previous_status
                        if previous_updated is None:
                            selected.pop('updated_at', None)
                        else:
                            selected['updated_at'] = previous_updated
                        error = '対応状況を保存できませんでした。時間をおいて再度お試しください。'
            elif action == 'create':
                # Keep accepting the previous form payload while existing clients transition.
                content = request.form.get('content', '').strip()
                urgency = request.form.get('urgency', '通常')
                shelter_name = request.form.get('shelter', '').strip()
                form_data = {
                    'content': content,
                    'urgency': urgency,
                    'shelter': shelter_name
                }
                if not content or len(content) > 500:
                    error = '指示内容は1〜500文字で入力してください。'
                elif urgency not in ('通常', '緊急'):
                    error = '緊急度を選択してください。'
                elif len(shelter_name) > 120:
                    error = '避難先は120文字以内で入力してください。'
                else:
                    existing_ids = [
                        item.get('id') for item in instructions
                        if isinstance(item, dict)
                        and isinstance(item.get('id'), int)
                        and not isinstance(item.get('id'), bool)
                    ]
                    now = get_japan_datetime()
                    instructions.append({
                        'id': max(existing_ids, default=0) + 1,
                        'target': '住民',
                        'content': content,
                        'urgency': urgency,
                        'shelter': shelter_name,
                        'status': '有効',
                        'created_at': format_datetime(now),
                        'created_at_iso': now.isoformat(),
                        'updated_at': format_datetime(now)
                    })
                    try:
                        save_instructions()
                        return redirect(url_for('board'))
                    except OSError:
                        instructions.pop()
                        error = '指示を保存できませんでした。時間をおいて再度お試しください。'
            elif action == 'update_status':
                instruction_id = request.form.get('instruction_id', '')
                status = request.form.get('status', '')
                if status not in ('解除', '終了') or not instruction_id.isdigit():
                    error = '指示の状態を更新できませんでした。'
                else:
                    selected = next(
                        (
                            item for item in instructions
                            if isinstance(item, dict)
                            and item.get('id') == int(instruction_id)
                            and instruction_is_active(item)
                        ),
                        None
                    )
                    if selected is None:
                        error = '有効な指示が見つかりません。'
                    else:
                        previous_status = selected.get('status')
                        previous_updated_at = selected.get('updated_at')
                        selected['status'] = status
                        selected['updated_at'] = get_japan_time()
                        try:
                            save_instructions()
                            return redirect(url_for('board'))
                        except OSError:
                            selected['status'] = previous_status
                            if previous_updated_at is None:
                                selected.pop('updated_at', None)
                            else:
                                selected['updated_at'] = previous_updated_at
                            error = '指示の状態を保存できませんでした。時間をおいて再度お試しください。'
            else:
                error = '不明な操作です。'
    board_instructions = [
        {**item, 'display_status': normalize_instruction_status(item.get('status'))}
        for item in instructions
        if isinstance(item, dict) and item.get('kind') != 'announcement'
    ]
    board_instructions.sort(
        key=instruction_priority_sort_key if sort_order == 'priority' else instruction_sort_key,
        reverse=sort_order == 'recent'
    )
    board_announcements = sorted(
        (
            item for item in instructions
            if isinstance(item, dict) and item.get('kind') == 'announcement'
        ),
        key=lambda item: instruction_sort_key({
            'created_at_iso': item.get('sent_at_iso'),
            'created_at': item.get('sent_at')
        }),
        reverse=True
    )
    return render_template(
        'board.html',
        instructions=board_instructions,
        announcements=board_announcements,
        csrf_token=get_csrf_token(),
        error=error,
        form_data=form_data,
        instruction_form_data=form_data.get('instruction', {}),
        announcement_form_data=form_data.get('announcement', {}),
        sort_order=sort_order,
        success_message=success_message,
        instruction_priorities=INSTRUCTION_PRIORITIES,
        instruction_statuses=INSTRUCTION_STATUSES
    )

# 検索結果ページ：templates/search_results.html を返す
@app.route('/search_results')
def search_results():
    keyword = request.args.get('keyword', '').strip()
    district = request.args.get('district', '').strip()
    selected_disaster_types = [
        value for value in request.args.getlist('disaster_types')
        if value in dict(DISASTER_TYPE_OPTIONS)
    ]
    selected_facilities = [
        value for value in request.args.getlist('facilities')
        if value in dict(FACILITY_OPTIONS)
    ]
    results = search_shelters(
        keyword,
        district,
        selected_disaster_types,
        selected_facilities
    )
    selected_conditions = [
        label for value, label in DISASTER_TYPE_OPTIONS
        if value in selected_disaster_types
    ] + [
        label for value, label in FACILITY_OPTIONS
        if value in selected_facilities
    ]
    search_args = {}
    if keyword:
        search_args['keyword'] = keyword
    if district:
        search_args['district'] = district
    if selected_disaster_types:
        search_args['disaster_types'] = selected_disaster_types
    if selected_facilities:
        search_args['facilities'] = selected_facilities
    return render_template(
        'search_results.html',
        results=results,
        keyword=keyword,
        district=district,
        selected_disaster_types=selected_disaster_types,
        selected_facilities=selected_facilities,
        selected_conditions=selected_conditions,
        search_url=url_for('shelter_search', **search_args),
        data_error=shelter_data_error
    )

# JSON API：/shelters?district=地区名
@app.route('/shelters', methods=['GET'])
def get_shelters():
    results = filter_shelters(request.args.get('district'))

    if not results:
        # 見つからなければエラー JSON を返す
        return jsonify({'error': 'No shelters found'}), 404

    # 見つかったらリストを JSON で返す
    return jsonify(results)

# 気象警報・注意報API
@app.route('/api/weather_warnings')
def api_weather_warnings():
    """気象警報・注意報をJSON形式で返すAPI"""
    return jsonify(get_weather_warnings())

if __name__ == '__main__':
    app.run(debug=True, port=5000)
