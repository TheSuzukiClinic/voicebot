from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse
import whisper, requests, tempfile, os
from urllib.parse import urlparse

model = whisper.load_model("base")
TWILIO_SID   = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_TOKEN = os.environ["TWILIO_AUTH_TOKEN"]
BASE_URL     = os.environ["BASE_URL"].rstrip("/")

app = Flask(__name__)

@app.route("/voice", methods=["POST"])
def voice():
    resp = VoiceResponse()
    resp.say("こんにちは。ご用件をどうぞ。ピーの後にお話しください。", language="ja-JP")
    resp.record(
        play_beep=True,
        timeout=3,
        max_length=20,
        action=f"{BASE_URL}/transcribe",
        method="POST"
    )
    return Response(str(resp), mimetype="application/xml")

@app.route("/transcribe", methods=["POST"])
def transcribe():
    rec_url = request.form.get("RecordingUrl")
    if not rec_url:
        return Response("Recording URL missing", status=400)

    audio_url = rec_url + ".mp3"
    parsed = urlparse(audio_url)
    if "twilio.com" in parsed.netloc:
        res = requests.get(audio_url, auth=(TWILIO_SID, TWILIO_TOKEN), timeout=10)
    else:
        res = requests.get(audio_url, timeout=10)
    if res.status_code != 200:
        return Response(f"Failed to download recording ({res.status_code})", status=500)

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp.write(res.content)
        tmp_path = tmp.name

    result = model.transcribe(tmp_path, language="ja")
    text = result["text"].strip()

    resp = VoiceResponse()
    if text:
        resp.say(f"文字起こしの結果です。{text}", language="ja-JP")
    else:
        resp.say("すみません。音声を認識できませんでした。", language="ja-JP")
    resp.hangup()
    return Response(str(resp), mimetype="application/xml")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5005, debug=False, use_reloader=False)
