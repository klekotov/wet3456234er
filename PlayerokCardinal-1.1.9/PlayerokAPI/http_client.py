"""
Выбор HTTP-бэкенда для запросов к playerok.com.

Основной бэкенд — ``curl_cffi``: он подставляет TLS/HTTP2-фингерпринт реального
браузера, что заметно снижает шанс попасть на проверку Cloudflare.

Если ``curl_cffi`` недоступен (не собрался/не установился на хосте),
используется запасной бэкенд на ``requests`` — бот продолжает работать,
но в лог пишется предупреждение.

Выбор можно зафиксировать переменной окружения ``POC_HTTP_BACKEND``:

* ``auto`` (по умолчанию) — ``curl_cffi``, если он импортируется, иначе ``requests``;
* ``curl_cffi`` — только ``curl_cffi`` (при отсутствии — ошибка);
* ``requests`` — принудительно ``requests``.
"""
from __future__ import annotations

import os
from logging import getLogger
from typing import Any

logger = getLogger("playerokapi")

BACKEND_ENV = "POC_HTTP_BACKEND"
BACKENDS = ("auto", "curl_cffi", "requests")

#: Бэкенд, который реально был выбран последним вызовом :func:`create_session`.
active_backend: str | None = None


def get_backend_mode() -> str:
    mode = (os.getenv(BACKEND_ENV) or "auto").strip().lower()
    return mode if mode in BACKENDS else "auto"


def curl_cffi_error() -> Exception | None:
    """Возвращает исключение импорта ``curl_cffi`` или ``None``, если он доступен."""
    try:
        import curl_cffi  # noqa: F401
    except Exception as ex:
        return ex
    return None


class RequestsSession:
    """
    Минимальная замена ``curl_cffi.Session`` на ``requests``.

    Поддерживает только то, что использует проект: ``get``/``post`` с
    ``params``, ``json``, ``data``, ``headers``, ``timeout``.
    """

    def __init__(
        self,
        proxy: str | None = None,
        timeout: float | None = None,
        verify: str | bool | None = None
    ):
        import requests

        self._requests = requests
        self._session = requests.Session()
        self._session.trust_env = False
        self.default_timeout = timeout
        if verify is not None:
            self._session.verify = verify
        self.proxies = (
            {"http": proxy, "https": proxy} if proxy else {}
        )

    def get(
        self,
        url: str,
        params: dict | None = None,
        headers: dict | None = None,
        timeout: float | None = None,
        **kwargs: Any
    ):
        return self._session.get(
            url,
            params=params,
            headers=headers,
            timeout=timeout or self.default_timeout,
            proxies=self.proxies,
            **kwargs
        )

    def post(
        self,
        url: str,
        data: Any = None,
        json: Any = None,
        headers: dict | None = None,
        timeout: float | None = None,
        **kwargs: Any
    ):
        return self._session.post(
            url,
            data=data,
            json=json,
            headers=headers,
            timeout=timeout or self.default_timeout,
            proxies=self.proxies,
            **kwargs
        )

    def close(self) -> None:
        self._session.close()


def create_session(
    proxy: str | None = None,
    timeout: float | None = None,
    verify: str | bool | None = None
):
    """
    Создаёт HTTP-сессию с учётом доступного бэкенда.

    :param proxy: Прокси в виде строки (``http://user:pass@host:port``).
    :param timeout: Таймаут запросов по умолчанию.
    :param verify: Путь до CA-файла (или ``False``, чтобы отключить проверку).

    :return: Кортеж ``(session, backend_name)``.
    :rtype: `tuple[object, str]`
    """

    global active_backend

    mode = get_backend_mode()
    error = curl_cffi_error()

    if mode == "curl_cffi" and error is not None:
        raise ModuleNotFoundError(
            f"{BACKEND_ENV}=curl_cffi, но модуль не импортируется: "
            f"{type(error).__name__}: {error}"
        ) from error

    if error is None and mode != "requests":
        import curl_cffi

        session = curl_cffi.Session(
            impersonate="chrome",
            timeout=timeout,
            proxy=proxy,
            verify=verify
        )
        backend = "curl_cffi"
    else:
        if mode != "requests":
            logger.warning(
                "curl_cffi недоступен (%s: %s) — используется резервный бэкенд "
                "requests. TLS-фингерпринт браузера НЕ подставляется, возможны "
                "проверки Cloudflare. Установите curl_cffi: "
                "python -m pip install -U -r requirements-tls.txt",
                type(error).__name__,
                error
            )
        else:
            logger.warning(
                "%s=requests — используется резервный бэкенд без TLS-фингерпринта.",
                BACKEND_ENV
            )
        session = RequestsSession(proxy=proxy, timeout=timeout, verify=verify)
        backend = "requests"

    active_backend = backend
    return session, backend
