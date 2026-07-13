import csv
import io

from django.http import HttpResponse


def csv_response(rows, filename="export.csv"):
    """
    Build an ``HttpResponse`` whose body is a UTF-8 CSV file.

    *rows* is an iterable of dicts — **all** rows must have the same keys.
    The first row's keys are used as the CSV header.
    """
    rows = list(rows)
    if not rows:
        return HttpResponse(
            "No data",
            content_type="text/plain; charset=utf-8",
            status=204,
        )

    header = list(rows[0].keys())
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=header)
    writer.writeheader()
    writer.writerows(rows)

    response = HttpResponse(
        buf.getvalue().encode("utf-8-sig"),
        content_type="text/csv; charset=utf-8-sig",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
