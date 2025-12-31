# This file is a part of IntelOwl https://github.com/intelowlproject/IntelOwl
# See the file 'LICENSE' for copying permission.

import datetime
from unittest.mock import patch

from api_app.analyzables_manager.models import Analyzable
from api_app.choices import Classification
from api_app.models import Job
from intel_owl.tasks import job_pipeline
from tests import CustomTestCase

_FAKE_NOW = datetime.datetime(2026, 8, 28, 12, 0, 0, tzinfo=datetime.timezone.utc)


@patch("intel_owl.tasks.now", return_value=_FAKE_NOW)
@patch("api_app.websocket.JobConsumer.serialize_and_send_job")
class JobPipelineExceptionTestCase(CustomTestCase):
    """
    Tests that job_pipeline correctly handles exceptions raised by job.execute().

    Regression test for: Job stuck in RUNNING forever when job_pipeline raises.
    The except block must:
      1. Set job.status = FAILED
      2. Set job.finished_analysis_time = now()
      3. Append the error to job.errors
      4. Persist via job.save()
      5. Push a WebSocket notification so the frontend stops spinning
    """

    def setUp(self):
        self.analyzable = Analyzable.objects.create(
            name="8.8.8.8",
            classification=Classification.IP,
        )
        self.job = Job.objects.create(
            user=self.superuser,
            status=Job.STATUSES.PENDING.value,
            analyzable=self.analyzable,
        )

    def tearDown(self):
        self.job.delete()
        self.analyzable.delete()

    def test_job_status_set_to_failed_on_exception(self, mock_ws, mock_now):
        """job.status must be FAILED after execute() raises — not left as RUNNING."""
        with patch.object(Job, "execute", side_effect=RuntimeError("broker down")):
            job_pipeline(self.job.pk)

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, Job.STATUSES.FAILED.value)

    def test_finished_analysis_time_set_on_exception(self, mock_ws, mock_now):
        """finished_analysis_time must be set so remove_old_jobs can clean the job up."""
        with patch.object(Job, "execute", side_effect=RuntimeError("broker down")):
            job_pipeline(self.job.pk)

        self.job.refresh_from_db()
        self.assertEqual(self.job.finished_analysis_time, _FAKE_NOW)

    def test_error_appended_to_job_errors_on_exception(self, mock_ws, mock_now):
        """Exception message must be recorded in job.errors."""
        with patch.object(Job, "execute", side_effect=RuntimeError("broker down")):
            job_pipeline(self.job.pk)

        self.job.refresh_from_db()
        self.assertIn("broker down", self.job.errors)

    def test_websocket_notification_sent_on_exception(self, mock_ws, mock_now):
        """Frontend must be notified immediately so the spinner stops."""
        with patch.object(Job, "execute", side_effect=RuntimeError("broker down")):
            job_pipeline(self.job.pk)

        mock_ws.assert_called_once_with(self.job)

    def test_no_exception_when_execute_succeeds(self, mock_ws, mock_now):
        """Happy path must not be broken by the fix."""
        with patch.object(Job, "execute", return_value=None):
            try:
                job_pipeline(self.job.pk)
            except Exception as e:
                self.fail(f"job_pipeline raised unexpectedly on success path: {e}")
