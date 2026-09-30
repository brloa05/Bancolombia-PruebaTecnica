"""Pruebas del asistente con IA, con el cliente de Ollama simulado (sin modelo ni red)."""

import json
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings

from . import ia
from .models import PropuestaIA

ID = "10020203023"
CONTEXTO = {
    "banca": "Privada",
    "perfil_riesgo_declarado": "MODERADO",
    "perfil_riesgo_implicito_por_volatilidad": "CONSERVADOR",
    "coherencia_perfil": "Riesgo por debajo del perfil",
    "volatilidad_anual_activos_pct": 1.2,
    "portafolio_total": "$ 2.113,0 millones de COP",
}
RESPUESTA_JSON = {
    "resumen": "Cliente de banca privada con portafolio conservador.",
    "puntos_clave": ["56 % en CDT"],
    "propuesta": "Proponer fondos multiactivo.",
    "guion_llamada": ["¿Cuál es su horizonte de inversión?"],
    "alertas": [],
}


def respuesta_falsa(contenido: str | None = None, done_reason: str = "stop"):
    return SimpleNamespace(
        model=ia.MODELO,
        done_reason=done_reason,
        message=SimpleNamespace(content=contenido if contenido is not None else json.dumps(RESPUESTA_JSON)),
        prompt_eval_count=1200,
        eval_count=450,
    )


def cliente_falso(contenido: str | None = None, done_reason: str = "stop"):
    cliente = mock.Mock()
    cliente.chat.return_value = respuesta_falsa(contenido, done_reason)
    return cliente


@mock.patch.object(ia, "contexto_cliente", return_value=CONTEXTO)
class GenerarPropuestaTests(TestCase):
    def test_guarda_la_propuesta_y_los_tokens(self, _):
        propuesta = ia.generar(ID, cliente=cliente_falso())
        self.assertEqual(PropuestaIA.objects.count(), 1)
        self.assertEqual(propuesta.contenido["propuesta"], "Proponer fondos multiactivo.")
        self.assertEqual((propuesta.tokens_entrada, propuesta.tokens_salida), (1200, 450))
        self.assertEqual(ia.ultima(ID), propuesta)

    def test_peticion_usa_el_modelo_local_y_el_esquema(self, _):
        cliente = cliente_falso()
        ia.generar(ID, cliente=cliente)
        kwargs = cliente.chat.call_args.kwargs
        self.assertEqual(kwargs["model"], ia.MODELO)
        self.assertEqual(kwargs["format"], ia.ESQUEMA)
        self.assertEqual(kwargs["messages"][0]["role"], "system")

    def test_no_envia_el_id_del_cliente(self, _):
        cliente = cliente_falso()
        ia.generar(ID, cliente=cliente)
        enviado = json.dumps(cliente.chat.call_args.kwargs, ensure_ascii=False, default=str)
        self.assertNotIn(ID, enviado)

    def test_respuesta_truncada_es_un_error(self, _):
        with self.assertRaises(ia.ErrorIA):
            ia.generar(ID, cliente=cliente_falso(done_reason="length"))
        self.assertEqual(PropuestaIA.objects.count(), 0)

    def test_json_invalido_es_un_error(self, _):
        with self.assertRaisesMessage(ia.ErrorIA, "JSON"):
            ia.generar(ID, cliente=cliente_falso(contenido="esto no es JSON"))

    def test_respuesta_sin_campos_requeridos_es_un_error(self, _):
        incompleta = json.dumps({"resumen": "solo resumen"})
        with self.assertRaisesMessage(ia.ErrorIA, "puntos_clave"):
            ia.generar(ID, cliente=cliente_falso(contenido=incompleta))

    def test_listas_devueltas_como_texto_se_normalizan(self, _):
        desviada = json.dumps({**RESPUESTA_JSON, "alertas": "Concentración alta", "extra": "ignorado"})
        propuesta = ia.generar(ID, cliente=cliente_falso(contenido=desviada))
        self.assertEqual(propuesta.contenido["alertas"], ["Concentración alta"])
        self.assertNotIn("extra", propuesta.contenido)


@mock.patch.object(ia, "contexto_cliente", return_value=CONTEXTO)
class VerificacionTests(TestCase):
    CONTRADICE = json.dumps({**RESPUESTA_JSON, "alertas": ["El perfil declarado es 'SIN DEFINIR'."]})

    def test_hechos_clave_van_explicitos_en_el_mensaje(self, _):
        cliente = cliente_falso()
        ia.generar(ID, cliente=cliente)
        mensaje = cliente.chat.call_args.kwargs["messages"][1]["content"]
        self.assertIn("Perfil de riesgo declarado: MODERADO", mensaje)

    def test_contradiccion_se_reintenta_y_se_corrige(self, _):
        cliente = mock.Mock()
        cliente.chat.side_effect = [respuesta_falsa(self.CONTRADICE), respuesta_falsa()]
        propuesta = ia.generar(ID, cliente=cliente)
        self.assertEqual(cliente.chat.call_count, 2)
        self.assertEqual(propuesta.contenido["verificacion"], [])

    def test_contradiccion_persistente_queda_advertida(self, _):
        cliente = mock.Mock()
        cliente.chat.side_effect = [respuesta_falsa(self.CONTRADICE), respuesta_falsa(self.CONTRADICE)]
        propuesta = ia.generar(ID, cliente=cliente)
        self.assertEqual(len(propuesta.contenido["verificacion"]), 1)
        self.assertIn("moderado", propuesta.contenido["verificacion"][0])

    def test_afirmar_coherencia_falsa_queda_advertida(self, _):
        falsa = json.dumps({**RESPUESTA_JSON, "resumen": "El perfil está en coherencia con el portafolio."})
        propuesta = ia.generar(ID, cliente=cliente_falso(contenido=falsa))
        self.assertIn("coherente", propuesta.contenido["verificacion"][0])

    def test_texto_coherente_no_genera_advertencias(self, _):
        coherente = json.dumps({**RESPUESTA_JSON, "resumen": "Perfil declarado moderado y perfil implícito conservador."})
        propuesta = ia.generar(ID, cliente=cliente_falso(contenido=coherente))
        self.assertEqual(propuesta.contenido["verificacion"], [])


class EstadoTests(TestCase):
    @mock.patch("ollama.Client.list", side_effect=ConnectionError("sin servidor"))
    def test_sin_ollama_informa_como_iniciarlo(self, _):
        disponible, mensaje = ia.estado()
        self.assertFalse(disponible)
        self.assertIn("ollama serve", mensaje)

    @mock.patch("ollama.Client.list", return_value=SimpleNamespace(models=[SimpleNamespace(model="otro:1b")]))
    def test_sin_el_modelo_indica_como_descargarlo(self, _):
        disponible, mensaje = ia.estado()
        self.assertFalse(disponible)
        self.assertIn(f"ollama pull {ia.MODELO}", mensaje)

    @override_settings(ALLOWED_HOSTS=["testserver"])
    @mock.patch.object(ia, "generar", side_effect=ia.ErrorIA("Ollama no está corriendo."))
    def test_vista_redirige_con_el_error(self, _):
        respuesta = self.client.post("/ia/propuesta/", {"cliente": ID})
        self.assertEqual(respuesta.status_code, 302)
        self.assertIn("#ia", respuesta["Location"])
