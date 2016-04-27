from lib.FileManager.workers.main.MainWorker import MainWorkerCustomer
import traceback


class RenameFile(MainWorkerCustomer):

    def __init__(self, source_path, target_path, *args, **kwargs):
        super(RenameFile, self).__init__(*args, **kwargs)

        self.source_path = source_path
        self.target_path = target_path

    def run(self):
        try:
            self.preload()

            source_abs_path = self.get_abs_path(self.source_path)
            target_abs_path = self.get_abs_path(self.target_path)

            self.logger.debug("FM NewFile worker run(), source_abs_path = %s" % source_abs_path)
            self.logger.debug("FM NewFile worker run(), target_abs_path = %s" % target_abs_path)

            try:
                source = self._make_file_info(source_abs_path)

                sftp = self.conn.open_sftp()
                sftp.rename(source_abs_path, target_abs_path)
                target = self._make_file_info(target_abs_path)

                result = {
                    "data": {
                        "source": source,
                        "target": target
                    },
                    "error": False,
                    "message": None,
                    "traceback": None
                }

                self.on_success(result)

            except OSError as e:
                result = {
                    "error": True,
                    "message": str(e),
                    "traceback": traceback.format_exc()
                }

                self.on_error(result)

        except Exception as e:
            result = {
                "error": True,
                "message": str(e),
                "traceback": traceback.format_exc()
            }

            self.on_error(result)
