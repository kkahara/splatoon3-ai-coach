"""Public error strings. None of these include paths, tokens, or storage keys."""

CAPACITY = "We're currently at capacity. Please try again later."
RATE_LIMIT = "Too many submissions from this network. Try again later."
VERIFY = "Submission could not be verified."
INTERRUPTED = "The analysis was interrupted. Submit the video again."
UPLOAD_MISSING = "The upload was not found."
UPLOAD_MISMATCH = "The uploaded file does not match this submission."
UPLOAD_UNFINISHED = "The upload was not finished."
PROBE_READ = "That file could not be read as a video."
PROBE_CONTAINER = "That file is not a supported video."
PROBE_STREAM = "That file has no video stream."
PROBE_DURATION = "That video is longer than 30 minutes."
PROBE_DIMENSIONS = "That video's dimensions are not supported."
PROBE_SIZE = "That video is too large."
ANALYSIS_FAILED = "Analysis failed."
COACHING_FAILED = "Coaching preparation failed."
EXPIRED = "This review has expired."
BAD_EMAIL = "Enter an email address to receive a notice."
BAD_FILE = "Choose an MP4 or MOV video."
BAD_SIZE = "That video is too large."
BAD_NAME = "Display name is not valid."
BAD_ACCOUNT_EMAIL = "Enter a valid email address."
BAD_ACCOUNT_NAME = "Enter your name."
BAD_PASSWORD = "Password must be at least 8 characters."
BAD_PASSWORD_LONG = "That password is too long."
EMAIL_TAKEN = "An account with that email already exists."
BAD_LOGIN = "Email or password is wrong."
UNVERIFIED = "Confirm your email before logging in."
BAD_LINK = "That link is no longer valid."
ACCOUNTS_UNAVAILABLE = "Accounts are unavailable."
LOGIN_REQUIRED = "Log in to continue."
BAD_FEEDBACK = "Enter your feedback."
BAD_FEEDBACK_LONG = "Feedback must be 2000 characters or fewer."
MAIL_FAILED = (
    "The confirmation email could not be sent. Brevo rejected this computer's "
    "address. Add it under authorized IPs, then try again."
)


class RequestRejected(Exception):
    """A public request that must not create or queue a submission."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)
