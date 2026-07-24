import os


class Analyzer:

    def analyze(self, filenames):

        total = {str(i): 0 for i in range(10)}

        files = {}

        for filename in filenames:

            path = os.path.join("files", filename)

            if not os.path.exists(path):
                continue

            try:
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
            except UnicodeDecodeError:
                continue

            stat = {str(i): 0 for i in range(10)}

            for ch in text:
                if ch.isdigit():
                    stat[ch] += 1
                    total[ch] += 1

            files[filename] = stat

        return {
            "total": total,
            "files": files
        }