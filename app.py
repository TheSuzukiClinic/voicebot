# app.py — Render用：Whisper API版（ローカルwhisper不使用）, BASE_URL不要
import os, time, json, tempfile, subprocess, requests
from flask import Flask, request, Response, url_for
from twilio.twiml.voice_response import VoiceResponse, Gather

app = Flask(__name__)

# --- 超ミニ後処理（日本語整形） ---
def clean_ja_transcript(text: str) -> str:
    if not text: return text
    m = {
        "hmo": "HMO", "ppo": "PPO",
        "じこふたん": "自己負担", "しんかん": "新患", "インシュアランス": "保険"
    }
    t = text
    for k,v in m.items(): t = t.replace(k, v)
    t = t.strip().replace("　"," ")
    while "  " in t: t = t.replace("  ", " ")
    if t and not any(p in t for p in "。.!?！？"): t += "。"
    return t

# --- ユーティリティ ---
def backoff_retry(fn, tries=3, base=0.8):
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            if i == tries-1: raise
            time.sleep(base*(2**i))
    raise last

def download_recording(url: str) -> bytes:
    r = requests.get(url, timeout=20); 
    if r.status_code == 200 and r.content: return r.content
    alt = url if url.endswith(".wav") else (url + ".wav")
    r2 = requests.get(alt, timeout=20); r2.raise_for_status(); 
    return r2.content

def to_pcm16k_mono(raw: bytes) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".in", delete=True) as fin, \
         tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fout:
        fin.write(raw); fin.flush()
        subprocess.check_call(
            ["ffmpeg","-y","-i",fin.name,"-ac","1","-ar","16000","-f","wav",fout.name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        with open(fout.name,"rb") as f: return f.read()

def whisper_transcribe_wav16(wav: bytes) -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key: raise RuntimeError("OPENAI_API_KEY is not set")
    url = "https://api.openai.com/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {api_key}"}
    files = {
        "file": ("audio.wav", wav, "audio/wav"),
        "model": (None, "whisper-1"),
        "language": (None, "ja"),
        "temperature": (None, "0")
    }
    resp = requests.post(url, headers=headers, files=files, timeout=60)
    resp.raise_for_status()
    return resp.json().get("text","")

def route_intent(text: str) -> str:
    t = (text or "").replace("　"," ").lower()
    if any(k in t for k in ["予約","アポイント","空き","とりたい","キャンセル"]): return "booking"
    if any(k in t for k in ["保険","インシュアランス","カバー","copay","自己負担","ppo","hmo"]): return "insurance"
    if any(k in t for k in ["自費","料金","値段","費用","価格"]): return "cashpay"
    return "other"

def reply_for(intent: str) -> str:
    if intent == "booking":
        return "予約のご希望ですね。新患の方は新患フォームをご記入ください。既存の方は候補日時をお知らせください。"
    if intent == "insurance":
        return "保険に関するご質問ですね。プランにより自己負担が異なります。具体的な金額は窓口でのご案内となります。"
    if intent == "cashpay":
        return "自費料金のご質問ですね。内容により費用が異なります。代表的な費用は当院ウェブサイトに掲載しています。"
    return "ご用件を承りました。内容を確認のうえ、折り返しご案内いたします。"

# --- Routes ---
@app.get("/health")
def health():
    return {"ok": True}, 200

@app.post("/voice")
def voice():
    vr = VoiceResponse()
    g = Gather(num_digits=1, action=url_for("menu", _external=True), timeout=5)
    g.say("お電話ありがとうございます。予約は1、保険は2、自費診療は3を押してください。オペレーターは0です。押さない場合は録音に進みます。", language="ja-JP")
    vr.append(g)
    vr.redirect(url_for("record", _external=True))
    return Response(str(vr), mimetype="text/xml")

@app.post("/menu")
def menu():
    digit = request.form.get("Digits","")
    vr = VoiceResponse()
    if digit == "1":
        vr.say("予約に関するメッセージをどうぞ。", language="ja-JP")
    elif digit == "2":
        vr.say("保険に関するご質問をどうぞ。", language="ja-JP")
    elif digit == "3":
        vr.say("自費診療に関するご質問をどうぞ。", language="ja-JP")
    elif digit == "0":
        vr.say("担当者におつなぎします。", language="ja-JP")
        # vr.dial("+1XXXXXXXXXX")
        return Response(str(vr), mimetype="text/xml")
    else:
        vr.say("選択が認識できませんでした。録音に進みます。", language="ja-JP")
    with vr.record(action=url_for("transcribe", _external=True), transcribe=False, max_length=90, play_beep=True):
        vr.say("ピー音の後、90秒まで録音できます。", language="ja-JP")
    return Response(str(vr), mimetype="text/xml")

@app.post("/record")
def record():
    vr = VoiceResponse()
    with vr.record(action=url_for("transcribe", _external=True), transcribe=False, max_length=90, play_beep=True):
        vr.say("ピー音の後にご用件をどうぞ。", language="ja-JP")
    return Response(str(vr), mimetype="text/xml")

@app.post("/transcribe")
def transcribe():
    recording_url = request.form.get("RecordingUrl")
    vr = VoiceResponse()
    try:
        audio = backoff_retry(lambda: download_recording(recording_url))
        wav16 = to_pcm16k_mono(audio)
        raw = backoff_retry(lambda: whisper_transcribe_wav16(wav16))
        text = clean_ja_transcript(raw)
        intent = route_intent(text)
        vr.say(reply_for(intent), language="ja-JP")
    except Exception:
        vr.say("うまく聞き取れませんでした。もう一度ゆっくりお話しください。", language="ja-JP")
    return Response(str(vr), mimetype="text/xml")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5005))
    app.run(host="0.0.0.0", port=port, debug=False)

