from lib.mail import send


def notify():
    return send("ops@example.test", "done")
