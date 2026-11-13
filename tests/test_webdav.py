from app.services.webdav import WebDAVClient


def test_parse_multistatus():
    xml = """<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">
    <d:response><d:href>/Groups/Geschichte/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response>
    <d:response><d:href>/Groups/Geschichte/AB.pdf</d:href><d:propstat><d:prop><d:displayname>AB.pdf</d:displayname><d:getetag>"abc"</d:getetag><d:getlastmodified>Mon, 21 Sep 2026 10:31:00 GMT</d:getlastmodified><d:getcontentlength>1234</d:getcontentlength><d:getcontenttype>application/pdf</d:getcontenttype><d:resourcetype/></d:prop></d:propstat></d:response>
    </d:multistatus>"""
    items = WebDAVClient.parse_multistatus(xml, "/Groups/Geschichte/")
    assert len(items) == 1
    assert items[0].filename == "AB.pdf"
    assert items[0].etag == '"abc"'
    assert items[0].content_length == 1234


def test_parse_multistatus_strips_webdav_base_path():
    xml = """<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">
    <d:response><d:href>/webdav/Groups/Geschichte/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response>
    <d:response><d:href>/webdav/Groups/Geschichte/Material/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response>
    <d:response><d:href>/webdav/Groups/Geschichte/AB.pdf</d:href><d:propstat><d:prop><d:displayname>AB.pdf</d:displayname><d:resourcetype/></d:prop></d:propstat></d:response>
    </d:multistatus>"""
    items = WebDAVClient.parse_multistatus(xml, "/Groups/Geschichte/", "/webdav")
    assert [item.relative_path for item in items] == ["/Groups/Geschichte/Material", "/Groups/Geschichte/AB.pdf"]


def test_root_url_keeps_webdav_base_path():
    client = WebDAVClient("https://example.invalid/webdav", "user", "password")
    assert client._url("/") == "https://example.invalid/webdav/"
    assert client._url("/webdav/Groups/Geschichte/") == "https://example.invalid/webdav/Groups/Geschichte/"
