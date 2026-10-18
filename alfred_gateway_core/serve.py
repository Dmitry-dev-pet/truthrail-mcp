"""One shared Peer Chat worker, bound exclusively to container loopback."""
from .peer_backend import server
from .transport_security import transport_security


def main() -> None:
    settings = transport_security()  # validate BEFORE opening any socket
    server.run(
        transport="streamable-http",
        host="127.0.0.1", port=18765,
        stateless_http=True,
        json_response=True,
        max_request_body_size=8192,
        transport_security=settings,
    )


if __name__ == "__main__":
    main()
