class Dispatcher:
    def handle(self, event):
        handler = getattr(self, f"on_{event}")
        handler()
        self.log(event)

    def on_create(self):
        pass

    def on_delete(self):
        pass

    def log(self, event):
        print(event)
