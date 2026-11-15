"""Early request and streamed file-size guards for import uploads."""

from django.conf import settings
from django.core.files.uploadhandler import FileUploadHandler, StopUpload
from django.http import JsonResponse


def _too_large_response():
    return JsonResponse({"detail": "Import request exceeds the configured size limit."}, status=413)


class LeadImportSizeLimitUploadHandler(FileUploadHandler):
    """Stop file bytes at the configured limit without passing excess to disk handlers."""

    def __init__(self, request=None):
        super().__init__(request)
        self.total_file_bytes = 0

    def receive_data_chunk(self, raw_data, start):
        self.total_file_bytes += len(raw_data)
        if self.total_file_bytes > settings.LEAD_IMPORT_MAX_FILE_SIZE:
            self.request._lead_import_upload_too_large = True
            raise StopUpload(connection_reset=False)
        return raw_data

    def file_complete(self, file_size):
        return None


class LeadImportUploadLimitMiddleware:
    """Reject oversized import requests before Django parses multipart bodies."""

    preview_path = "/api/v1/leads/import/preview/"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path_info != self.preview_path or request.method != "POST":
            return self.get_response(request)

        raw_length = request.META.get("CONTENT_LENGTH")
        if not raw_length:
            return JsonResponse({"detail": "Content-Length is required for import uploads."}, status=411)
        try:
            content_length = int(raw_length)
        except (TypeError, ValueError):
            return JsonResponse({"detail": "Invalid request size."}, status=400)
        if content_length < 0:
            return JsonResponse({"detail": "Invalid request size."}, status=400)
        if content_length > settings.LEAD_IMPORT_MAX_REQUEST_SIZE:
            return _too_large_response()

        request._lead_import_upload_too_large = False
        request.upload_handlers.insert(0, LeadImportSizeLimitUploadHandler(request))
        response = self.get_response(request)
        if request._lead_import_upload_too_large:
            return _too_large_response()
        return response
