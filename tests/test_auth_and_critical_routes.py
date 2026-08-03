"""Testes de API de ponta a ponta para os fluxos mais críticos: login/sessão,
gates de autorização (login_required/admin_required baseado em User.is_admin)
e o fluxo de filiais/apostas (ledger). Escritas via HTTP acontecem dentro da
mesma transação de `db_session`, que é revertida ao final — não deixam dados
no banco compartilhado (Supabase).
"""

import pytest
from fastapi.testclient import TestClient

from palpitaria.database import get_db
from palpitaria.main import app
from palpitaria.models import Bet, Branch, User
from palpitaria.services.auth import get_password_hash


@pytest.fixture
def tx_client(db_session):
    """TestClient cujo `get_db` é sobrescrito para usar a sessão transacional
    do teste — qualquer escrita feita através de uma rota HTTP é revertida
    junto com `db_session` no teardown."""

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def _make_user(db_session, *, email: str, password: str = "Senha123!456", is_admin: bool = False) -> User:
    user = User(
        email=email,
        hashed_password=get_password_hash(password),
        is_active=True,
        is_admin=is_admin,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _login(tx_client, email: str, password: str):
    return tx_client.post(
        "/login",
        data={"email": email, "password": password, "accept_terms": "on"},
        follow_redirects=False,
    )


def test_login_success_redirects_home(tx_client, db_session):
    _make_user(db_session, email="furlan-teste@example.com")

    resp = _login(tx_client, "furlan-teste@example.com", "Senha123!456")

    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_login_wrong_password_shows_error_without_creating_session(tx_client, db_session):
    _make_user(db_session, email="furlan-teste2@example.com")

    resp = _login(tx_client, "furlan-teste2@example.com", "senha-errada")

    assert resp.status_code == 200
    assert "inválidos" in resp.text

    # Sem sessão válida: rota protegida deve redirecionar para /login, não retornar 200.
    protected = tx_client.get("/branches", follow_redirects=False)
    assert protected.status_code == 303
    assert protected.headers["location"] == "/login"


def test_login_requires_accept_terms(tx_client, db_session):
    _make_user(db_session, email="furlan-teste3@example.com")

    resp = tx_client.post(
        "/login",
        data={"email": "furlan-teste3@example.com", "password": "Senha123!456"},
        follow_redirects=False,
    )

    assert resp.status_code == 200
    assert "Aviso Legal" in resp.text


def test_anonymous_user_is_redirected_from_protected_route(tx_client):
    resp = tx_client.get("/branches", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_admin_required_blocks_non_admin_user(tx_client, db_session):
    _make_user(db_session, email="furlan-comum@example.com", is_admin=False)
    _login(tx_client, "furlan-comum@example.com", "Senha123!456")

    resp = tx_client.get("/admin/users")
    assert resp.status_code == 403


def test_admin_required_allows_admin_user(tx_client, db_session):
    _make_user(db_session, email="furlan-admin@example.com", is_admin=True)
    _login(tx_client, "furlan-admin@example.com", "Senha123!456")

    resp = tx_client.get("/admin/users")
    assert resp.status_code == 200


def test_regular_user_can_access_own_branches_page(tx_client, db_session):
    _make_user(db_session, email="furlan-branches@example.com", is_admin=False)
    _login(tx_client, "furlan-branches@example.com", "Senha123!456")

    resp = tx_client.get("/branches")
    assert resp.status_code == 200


def test_branch_and_bet_roundtrip_via_http(tx_client, db_session):
    user = _make_user(db_session, email="furlan-ledger@example.com", is_admin=False)
    _login(tx_client, "furlan-ledger@example.com", "Senha123!456")

    add_branch_resp = tx_client.post(
        "/branches/add",
        data={
            "name": "Teste Over 2.5",
            "description": "Filial de teste automatizado",
            "commission_rate": "6.5",
            "side": "BACK",
        },
        follow_redirects=False,
    )
    assert add_branch_resp.status_code == 303

    branch = (
        db_session.query(Branch)
        .filter(Branch.user_id == user.id, Branch.name == "Teste Over 2.5")
        .one_or_none()
    )
    assert branch is not None
    assert branch.side == "BACK"

    add_bet_resp = tx_client.post(
        "/branches/add-bet",
        data={
            "branch_id": str(branch.id),
            "competition_code": "BSA",
            "description": "Flamengo x Palmeiras",
            "odds": "1.85",
            "stake": "50",
            "outcome": "PENDING",
        },
        follow_redirects=False,
    )
    assert add_bet_resp.status_code == 303

    bet = (
        db_session.query(Bet)
        .filter(Bet.branch_id == branch.id, Bet.description == "Flamengo x Palmeiras")
        .one_or_none()
    )
    assert bet is not None
    assert bet.odds == 1.85
    assert bet.stake == 50.0
    assert bet.outcome == "PENDING"


def test_add_bet_rejects_missing_competition_code(tx_client, db_session):
    user = _make_user(db_session, email="furlan-ledger2@example.com", is_admin=False)
    _login(tx_client, "furlan-ledger2@example.com", "Senha123!456")

    branch = Branch(
        name="Filial sem comp",
        slug=f"filial_sem_comp_{user.id}",
        description="",
        user_id=user.id,
    )
    db_session.add(branch)
    db_session.commit()
    db_session.refresh(branch)

    resp = tx_client.post(
        "/branches/add-bet",
        data={
            "branch_id": str(branch.id),
            "competition_code": "",
            "description": "Jogo qualquer",
            "odds": "1.5",
            "stake": "10",
            "outcome": "PENDING",
        },
    )
    assert resp.status_code == 400


def test_add_bet_rejects_branch_from_another_user(tx_client, db_session):
    other_user = _make_user(db_session, email="furlan-outro@example.com", is_admin=False)
    other_branch = Branch(
        name="Filial de outro usuário",
        slug=f"filial_outro_{other_user.id}",
        description="",
        user_id=other_user.id,
    )
    db_session.add(other_branch)
    db_session.commit()
    db_session.refresh(other_branch)

    _make_user(db_session, email="furlan-atacante@example.com", is_admin=False)
    _login(tx_client, "furlan-atacante@example.com", "Senha123!456")

    resp = tx_client.post(
        "/branches/add-bet",
        data={
            "branch_id": str(other_branch.id),
            "competition_code": "BSA",
            "description": "Tentativa indevida",
            "odds": "1.5",
            "stake": "10",
            "outcome": "PENDING",
        },
    )
    assert resp.status_code == 403
