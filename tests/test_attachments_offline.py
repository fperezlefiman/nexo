"""Adjuntos: el canal no los tira, los convierte en una ruta para el turno."""

from nexo.channels.telegram import attachment_of


def test_photo_picks_the_largest_size():
    msg = {"photo": [{"file_id": "small", "file_size": 10}, {"file_id": "big", "file_size": 900}]}
    att = attachment_of(msg)
    assert att["kind"] == "photo"
    assert att["file_id"] == "big"
    assert att["mime"] == "application/octet-stream"


def test_document_keeps_its_name_and_mime():
    msg = {"document": {"file_id": "abc", "file_name": "captura.png",
                        "mime_type": "image/png", "file_size": 2048}}
    att = attachment_of(msg)
    assert att == {"kind": "document", "file_id": "abc", "name": "captura.png",
                   "mime": "image/png", "size": 2048}


def test_text_only_message_has_no_attachment():
    assert attachment_of({"text": "hola"}) is None


def test_voice_note_counts_as_attachment():
    att = attachment_of({"voice": {"file_id": "v1", "file_size": 512}})
    assert att["kind"] == "voice"
