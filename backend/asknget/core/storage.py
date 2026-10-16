"""File storage that understands a name which is already a full web address.

An image field holds a file name that `url()` turns into an address. Where uploads cannot be kept (a Vercel deployment's disk is
read-only) a picture can still be a link to somewhere else: store its full `https://...` address as the name and `url()` returns
it unchanged, so every serializer that reads `image.url` works without knowing the difference. The same override belongs on an
object-storage backend when one replaces this."""

from django.core.files.storage import FileSystemStorage


class UrlFriendlyStorage(FileSystemStorage):
    def url(self, name):
        if name and name.startswith(("http://", "https://")):
            return name
        return super().url(name)
