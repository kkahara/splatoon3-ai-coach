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


class RequestRejected(Exception):
    """A public request that must not create or queue a submission."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)
