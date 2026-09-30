"""Pruebas del asistente con IA, con el cliente de Anthropic simulado (sin costo ni red)."""

import json
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings

from . import ia
from .models import PropuestaIA

ID = "10020203023"
CONTEXTO = {"banca": "Privada", "perfil_riesgo_declarado": "MODERADO", "portafolio_total_cop_millones": 2113.0}
RESPUESTA_JSON = {
    "resumen": "Cliente de banca privada con portafolio conservador.",
    "puntos_clave": ["56 % en CDT"],
    "propuesta": "Proponer fondos multiactivo.",
    "guion_llamada": ["¿Cuál es su horizonte de inversión?"],
    "alertas": [],
}


def cliente_falso(stop_reason: str = "end_turn", contenido: dict | None = None):
    respuesta = SimpleNamespace(
        stop_reason=stop_reason,
        model=ia.MODELO,
        content=[SimpleNamespace(type="text", text=json.dumps(contenido or RESPUESTA_JSON))],
        usage=SimpleNamespace(input_tokens=1200, output_tokens=450),
    )
    cliente = mock.Mock()
    cliente.beta.messages.create.return_value = respuesta
    return cliente


@mock.patch.object(ia, "contexto_cliente", return_value=CONTEXTO)
class GenerarPropuestaTests(TestCase):
    def test_guarda_la_propuesta_y_los_tokens(self, _):
        propuesta = ia.generar(ID, cliente=cliente_falso())
        self.assertEqual(PropuestaIA.objects.count(), 1)
        self.assertEqual(propuesta.contenido["propuesta"], "Proponer fondos multiactivo.")
        self.assertEqual((propuesta.tokens_entrada, propuesta.tokens_salida), (1200, 450))
        self.assertEqual(ia.ultima(ID), propuesta)

    def test_peticion_usa_modelo_esquema_y_fallback(self, _):
        cliente = cliente_falso()
        ia.generar(ID, cliente=cliente)
        kwargs = cliente.beta.messages.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "claude-opus-5-5")
        self.assertEqual(kwargs["fallbacks"], "default")
        self.assertIn("server-side-fallback-2026-07-01", kwargs["betas"])
        self.assertEqual(kwargs["output_config"]["format"]["schema"], ia.ESQUEMA)

    def test_no_envia_el_id_del_cliente(self, _):
        cliente = cliente_falso()
        ia.generar(ID, cliente=cliente)
        enviado = json.dumps(cliente.beta.messages.create.call_args.kwargs, ensure_ascii=False, default=str)
        self.assertNotIn(ID, enviado)

    def test_rechazo_del_modelo_no_guarda_nada(self, _):
        with self.assertRaises(ia.ErrorIA):
            ia.generar(ID, cliente=cliente_falso(stop_reason="refusal"))
        self.assertEqual(PropuestaIA.objects.count(), 0)

    def test_respuesta_truncada_es_un_error(self, _):
        with self.assertRaises(ia.ErrorIA):
            ia.generar(ID, cliente=cliente_falso(stop_reason="max_tokens"))


class SinApiKeyTests(TestCase):
    @mock.patch.dict("os.environ", {}, clear=True)
    def test_sin_api_key_informa_el_error(self):
        self.assertFalse(ia.disponible())
        with self.assertRaisesMessage(ia.ErrorIA, "ANTHROPIC_API_KEY"):
            ia.generar(ID)

    @mock.patch.dict("os.environ", {}, clear=True)
    @override_settings(ALLOWED_HOSTS=["testserver"])
    def test_vista_redirige_con_mensaje(self):
        respuesta = self.client.post("/ia/propuesta/", {"cliente": ID}, follow=False)
        self.assertEqual(respuesta.status_code, 302)
        self.assertIn("#ia", respuesta["Location"])
