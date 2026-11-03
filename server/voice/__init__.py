"""Server side of voice input for Agent mode (speech to text).

``audio``    validate and convert browser audio to Meta-ready WAV.
``service``  Meta ASR over HTTP, or a labelled RECORDED fixture; usage cap.
``routes``   POST /voice/transcribe, GET /voice/status.
"""
