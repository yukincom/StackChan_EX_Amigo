# ai_handler.py

import re
from datetime import datetime

from config import config
from member_loader import (
    MASTER_MASK_LABEL,
    get_speaker_mask_label_map,
    load_member_data,
    mask_names,
    unmask_names,
)
import llm_client

def needs_search(text):
    """検索キーワードを含む発話はWeb検索を使う"""
    return any(kw in text for kw in config.SEARCH_KEYWORDS)

def _is_english(text):
    """テキストが主に英語かどうか判定（英字比率50%以上）"""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    en_count = sum(1 for c in letters if ord(c) < 128)
    return (en_count / len(letters)) >= 0.5


def _wants_english_reply(text: str) -> bool:
    normalized = (text or "").strip()
    normalized_lower = normalized.lower()
    keywords = ("英語で", "英語に", "in english")
    return any(keyword in normalized or keyword in normalized_lower for keyword in keywords)

def detect_intent(user_text):
    """発話の意図を判定して返す

    Returns:
        'english_reply' : 英語入力または英語返答リクエスト
        'normal'        : それ以外
    """
    if _is_english(user_text) or _wants_english_reply(user_text):
        return "english_reply"

    return "normal"

# 英語モード
_english_mode = {
    "active": False,
}
def set_english_mode(active: bool):
    _english_mode["active"] = active
    print(f"[INTENT] 英語モード {'ON' if active else 'OFF'}")

def is_english_mode() -> bool:
    return _english_mode["active"]

def _remove_katakana_reading(text: str) -> str:
    """「英単語」の後に続くカタカナ読みを除去する"""
    # 「発音は〜って感じ」パターンを除去
    text = re.sub(r'[。、]?発音は[「」ァ-ヶー・\s]+(?:って感じ)?(?:かな)?', '', text)
    # 英単語直後のカタカナ括弧を除去（例：school（スクール））
    text = re.sub(r'([A-Za-z\"\'])\s*[（(][ァ-ヶー・]+[）)]', r'\1', text)
    return text.strip()

def get_ai_response(user_text, context, speaker, mode="normal"):
    """AIで応答生成（llm_client経由・プロバイダー切り替え対応）"""
    # ★ AIに渡す前に名前をマスク
    masked_text = mask_names(user_text)

    recent_text = ""
    for conv in context.get("recent_conversations", []):
        recent_text += f"  {mask_names(conv['user'])} → {mask_names(conv['assistant'])}\n"

    # ★ 家族構成もマスク
    family_text = chr(10).join(
        f"- {mask_names(name)}: {role}"
        for name, role in context.get('family', {}).items()
    )
    notes_text = mask_names(context.get('notes', '注釈なし'))
    game_text = ""
    for game in context.get("game_progress", []):
        game_text += f"  {game['game_name']}: {game['progress']}\n"

    if needs_search(user_text):
        sentence_rule = "- 回答は検索結果の内容を優先してね！3〜4文、150文字程度でまとめてね！"
    else:
        sentence_rule = "- 2文程度、50文字以内でまとめてね！"
    
    members = load_member_data()
    _speaker_label_map = get_speaker_mask_label_map(members)
    _master_ref = MASTER_MASK_LABEL
    speaker_ref = _speaker_label_map.get(speaker, _master_ref)

    if speaker.startswith("family"):
        speaker_rule = f"- 今話しかけているのは{speaker_ref}です。{speaker_ref}に向けて返答してください。{_master_ref}には話しかけないでください。"
    elif speaker == "other":
        speaker_rule = f"- 話者は{_master_ref}ではありません。返答に「{_master_ref}」という名前を含めないでください。"
    else:
        speaker_rule = f"- 話者は{_master_ref}です。"

    mode_rule = ""
    if mode == "english_reply":
        mode_rule = "- 英語で自然に返答してください。"


    # マスクラベルの呼び名ルールを動的生成
    family_call_rules = "\n".join(
        f"- [家族{i}]のことは「[家族{i}]」と、角括弧付きでそのまま呼んでください。"
        for i, _member in enumerate(members.get("family", []), 1)
    )

    prompt = f"""
    あなたの名前は「{config.ASSISTANT_NAME}」。{context.get('persona', '')} {config.ASSISTANT_PERSONA}

# 家族構成
{family_text} 

# 最近の会話
{recent_text if recent_text else '今日はまだ会話していない'} 

# ゲーム進行状況
{game_text if game_text else 'ゲーム情報なし'}

# マスターについての注釈
{notes_text} 

現在時刻: {datetime.now().strftime('%Y年%m月%d日 %H:%M')}
話者: {speaker}

会話のルール:
{mode_rule}
- {sentence_rule} 
- {speaker_rule}
- {MASTER_MASK_LABEL}のことは「{MASTER_MASK_LABEL}」と、そのまま呼んでください。
- {MASTER_MASK_LABEL}や家族ラベルを、[section]...[/section] のようなタグやマークアップで囲まないでください。
- {family_call_rules}
- 「マスター」「お母さん」などの裸の役割名は使わず、呼びかける必要があるときは角括弧付きラベルだけを使ってください。
- userの発言を繰り返したり、言い換えたりしないでね！
- 直近、５件程度の出力にuserの名前がある場合は、userの名前を入れないでね！
- 直近、５件程度の出力に「お」とか「あ」のような感嘆がある場合は、感嘆を入れないようにしてね。
- 「最近の会話」を参照し、話題が続いている場合はそれまでの文脈を踏まえて返答する
- 以下の「好きなもの」に関するキーワードが出たら、前の文脈と自然に繋げる
  {context.get('interests', '')}
- わからないことは必要以上に想像で補わず、「わからないなー」と正直に回答してください。
- 「おはよう」等の挨拶は、された時だけ返してね！
- 「〜だよ」「〜だな」「〜じゃない？」など親しみやすい口調
- できないお願いは「ごめんね、それ僕にはできないんだ〜」って優しく断ってね。
- あなたは必要に応じて自分のカメラを使って、周囲の環境や目の前の物を確認できます。
- ユーザーが「これ」「それ」「あれ」など今ある物を指していて、視覚確認しないと判断できない場合は、会話文より先にカメラ確認を優先してください。
- 周りの様子を確認したいとき、ユーザーが見せている物を確認したいとき、見ないと答えられないときだけ、会話文の代わりに {config.AI_VISION_TRANSIENT_MARKER} だけを返してください。
- {config.AI_VISION_TRANSIENT_MARKER} や {config.AI_VISION_ARCHIVE_MARKER} を返すときは、前後に他の文字を絶対につけないでください。
- カメラが不要なときは、{config.AI_VISION_TRANSIENT_MARKER} や {config.AI_VISION_ARCHIVE_MARKER} を絶対に出さないでください。
- 例:
  - user: 「これ何？」 -> {config.AI_VISION_TRANSIENT_MARKER}
  - user: 「これ知ってる？」 -> {config.AI_VISION_TRANSIENT_MARKER}
  - user: 「同じものかな？」 -> {config.AI_VISION_TRANSIENT_MARKER}
  - user: 「これって何に使うの？」 -> {config.AI_VISION_TRANSIENT_MARKER}
  - user: 「今なにが見えてる？」 -> {config.AI_VISION_TRANSIENT_MARKER}
  - user: 「今何時？」 -> 通常の会話文で返答

ユーザー: {masked_text} 
アシスタント: """
    try:
        if needs_search(user_text):
            raw = llm_client.call_search(prompt)
        else:
            raw = llm_client.call(prompt)
        ai_text = unmask_names(raw)
        ai_text = _remove_katakana_reading(ai_text)
        return ai_text
    except Exception as e:
        print(f"[ERROR] LLM エラー: {e}")
        return "ごめん、今ちょっと調子悪いや...後でまた話そうね！"
