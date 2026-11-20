"""Lectura de la página de origen de una pista (sin red)."""
from fabrica.musica_release import leer_pagina

PAGINA = """
<div class="field field-name-author-submitter field-type-ds field-label-inline"><div class="field-label">Author:&nbsp;</div><div class="field-items"><div class="field-item even"><span class="username"><a href="/users/pmiller">pmiller</a></span></div></div></div>
<div class="field field-name-field-art-licenses field-type-taxonomy-term-reference field-label-above"><div class="field-label">License(s):&nbsp;</div><div class="field-items"><div class="field-item even"><div class='license-icon'><a href='http://creativecommons.org/publicdomain/zero/1.0/' target='_blank'><img src='/sites/default/files/license_images/cc0.png' alt='' title=''><div class='license-name'>CC0</div></a></div></div></div></div>
<div class="field field-name-field-art-files field-type-file field-label-above"><div class="field-label">File(s):&nbsp;</div><div class="field-items"><div class="field-item even"><span class="file"><a href="https://opengameart.org/sites/default/files/calm_track.ogg">calm_track.ogg</a></span></div><div class="field-item odd"><span class="file"><a href="/sites/default/files/n%20os.mp3">n os.mp3</a></span></div></div></div>
<div class="field field-name-field-copyright-notice"></div>
"""


def test_lee_licencia_autor_y_archivos():
    r = leer_pagina(PAGINA, "https://opengameart.org/content/calm-track")
    assert r["licencias"] == ["CC0"]
    assert r["autor"] == "pmiller"
    assert r["archivos"]["calm_track.ogg"] == "https://opengameart.org/sites/default/files/calm_track.ogg"
    assert r["archivos"]["n os.mp3"] == "https://opengameart.org/sites/default/files/n%20os.mp3"


def test_licencia_ajena_no_es_cc0():
    otra = PAGINA.replace("CC0</div>", "CC-BY 4.0</div>").replace("publicdomain/zero", "licenses/by")
    assert leer_pagina(otra, "https://opengameart.org/x")["licencias"] == ["CC-BY 4.0"]


def test_sin_campo_de_licencia():
    assert leer_pagina("<html></html>", "https://opengameart.org/x")["licencias"] == []
