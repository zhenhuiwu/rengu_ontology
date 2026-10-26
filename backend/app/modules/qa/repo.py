"""问数数据访问：只写本模块特有的查询，分页 / 按编号取沿用 BaseRepository。"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Select, and_, case, exists, func, literal, or_, select
from sqlalchemy.orm import aliased, selectinload

from app.common.repository import BaseRepository
from app.modules.admin.models import SysUser
from app.modules.qa.models import (
    CertStatus,
    FbStatus,
    QaCert,
    QaCertVariant,
    QaEvalFail,
    QaEvalQuestion,
    QaEvalRun,
    QaFavorite,
    QaFeedback,
    QaFreqQuestion,
    QaObjectTypeView,
    QaPage,
    QaPageShareMember,
    QaSession,
    QaSessionOption,
    QaSynonymLink,
    QaTemplate,
    RunStatus,
    ShareMode,
)


async def user_pk(session, code: str) -> int | None:
    return await session.scalar(select(SysUser.id).where(SysUser.code == code))


class SessionRepo(BaseRepository[QaSession]):
    model = QaSession
    not_found_message = "会话不存在"

    def list_query(self, owner_pk: int, q: str | None, since: datetime | None) -> Select:
        stmt = self.base_query().where(QaSession.owner_id == owner_pk)
        if q:
            stmt = stmt.where(QaSession.title.contains(q, autoescape=True))
        if since is not None:
            stmt = stmt.where(QaSession.last_at >= since)
        return stmt.order_by(QaSession.last_at.desc(), QaSession.code)

    async def detail(self, code: str) -> QaSession | None:
        stmt = self.base_query().where(QaSession.code == code).options(selectinload(QaSession.turns))
        return (await self.session.scalars(stmt)).unique().one_or_none()


class FavoriteRepo(BaseRepository[QaFavorite]):
    model = QaFavorite

    def list_query(self, owner_pk: int, q: str | None) -> Select:
        stmt = self.base_query().where(QaFavorite.owner_id == owner_pk)
        if q:
            stmt = stmt.where(QaFavorite.question.contains(q, autoescape=True))
        return stmt.order_by(QaFavorite.uses.desc(), QaFavorite.code)


class OptionRepo(BaseRepository[QaSessionOption]):
    model = QaSessionOption

    async def by_kind(self, kind: str) -> Sequence[QaSessionOption]:
        stmt = self.base_query().where(QaSessionOption.kind == kind).order_by(QaSessionOption.sort_order)
        return (await self.session.scalars(stmt)).all()


_PAGE_LOAD = (selectinload(QaPage.cards), selectinload(QaPage.members), selectinload(QaPage.subscription))


class PageRepo(BaseRepository[QaPage]):
    model = QaPage
    not_found_message = "页面不存在"

    def visible_clause(self, viewer_pk: int, viewer_dept: str | None, *, include_shared: bool):
        """我的页面；include_shared 时再加上共享给我的（指定成员含我 / 所在部门且我的部门包含拥有者部门）。"""
        own = QaPage.owner_id == viewer_pk
        if not include_shared:
            return own
        member = and_(
            QaPage.share_mode == ShareMode.members,
            exists().where(QaPageShareMember.page_id == QaPage.id, QaPageShareMember.user_id == viewer_pk),
        )
        clauses = [own, member]
        if viewer_dept:
            owner = aliased(SysUser)
            clauses.append(
                and_(
                    QaPage.share_mode == ShareMode.dept,
                    exists().where(
                        owner.id == QaPage.owner_id,
                        owner.dept.is_not(None),
                        func.strpos(literal(viewer_dept), owner.dept) > 0,
                    ),
                )
            )
        return or_(*clauses)

    def list_query(
        self,
        visible,
        *,
        kw: str | None = None,
        src=None,
        shared: bool | None = None,
        status=None,
        full: bool = False,
    ) -> Select:
        stmt = self.base_query().where(visible)
        if kw:
            stmt = stmt.where(QaPage.name.contains(kw, autoescape=True))
        if src is not None:
            stmt = stmt.where(QaPage.source_kind == src)
        if shared is not None:
            stmt = stmt.where(
                (QaPage.share_mode != ShareMode.private)
                if shared
                else (QaPage.share_mode == ShareMode.private)
            )
        if status is not None:
            stmt = stmt.where(QaPage.status == status)
        stmt = stmt.options(selectinload(QaPage.cards))
        if full:
            stmt = stmt.options(*_PAGE_LOAD)
        # 新建的在前；种子同一时刻写入，按编号（= mock 数组顺序）。
        # dev/test 时钟固定，新建页和种子 created_at 相同 → 非种子排前、后建的在前（P2，qa-pages）
        return stmt.order_by(
            QaPage.created_at.desc(),
            QaPage.is_seed,
            case((QaPage.is_seed.is_(False), -QaPage.id), else_=0),
            QaPage.code,
        )

    async def is_visible(self, page_pk: int, visible) -> bool:
        return bool(await self.session.scalar(select(QaPage.id).where(QaPage.id == page_pk, visible)))

    async def detail(self, code: str) -> QaPage | None:
        stmt = self.base_query().where(QaPage.code == code).options(*_PAGE_LOAD)
        return (await self.session.scalars(stmt)).unique().one_or_none()


class TemplateRepo(BaseRepository[QaTemplate]):
    model = QaTemplate
    not_found_message = "模板不存在"

    def list_query(self, kw: str | None, scene: str | None) -> Select:
        stmt = self.base_query().options(selectinload(QaTemplate.cards))
        if kw:
            stmt = stmt.where(QaTemplate.name.contains(kw, autoescape=True))
        if scene:
            stmt = stmt.where(QaTemplate.scene == scene)
        return stmt.order_by(QaTemplate.code)


class ObjectTypeRepo(BaseRepository[QaObjectTypeView]):
    model = QaObjectTypeView

    async def current(self) -> Sequence[QaObjectTypeView]:
        """[推断] 每个 kind 取 release_id 最大的一行（种子行 release_id 为空）。"""
        rn = (
            func.row_number()
            .over(
                partition_by=QaObjectTypeView.kind, order_by=QaObjectTypeView.release_id.desc().nulls_last()
            )
            .label("rn")
        )
        sub = select(QaObjectTypeView.id, rn).subquery()
        stmt = (
            select(QaObjectTypeView)
            .join(sub, sub.c.id == QaObjectTypeView.id)
            .where(sub.c.rn == 1)
            .order_by(QaObjectTypeView.sort_order, QaObjectTypeView.kind)
        )
        return (await self.session.scalars(stmt)).all()


_CERT_RANK = case(
    (QaCert.status == CertStatus.recert, 0),
    (QaCert.status == CertStatus.pending, 1),
    (QaCert.status == CertStatus.certified, 2),
    else_=3,
)


class CertRepo(BaseRepository[QaCert]):
    model = QaCert
    not_found_message = "认证问答不存在"

    def list_query(self, kw: str | None, status, metric: str | None) -> Select:
        stmt = self.base_query()
        if kw:
            stmt = stmt.where(
                or_(
                    QaCert.question.contains(kw, autoescape=True),
                    exists().where(
                        QaCertVariant.cert_id == QaCert.id,
                        QaCertVariant.question.contains(kw, autoescape=True),
                    ),
                )
            )
        if status is not None:
            stmt = stmt.where(QaCert.status == status)
        if metric:
            stmt = stmt.where(QaCert.sem_plan["metric"].as_string() == metric)
        return stmt.order_by(_CERT_RANK, QaCert.created_at.desc(), QaCert.code)

    async def count_status(self, status) -> int:
        return int(await self.session.scalar(select(func.count()).where(QaCert.status == status)) or 0)

    async def by_question(self, question: str) -> QaCert | None:
        return (await self.session.scalars(self.base_query().where(QaCert.question == question))).first()


class EvalRunRepo(BaseRepository[QaEvalRun]):
    model = QaEvalRun
    not_found_message = "评测运行不存在"

    def list_query(self, trigger: str | None) -> Select:
        stmt = self.base_query()
        if trigger:
            stmt = stmt.where(QaEvalRun.trigger_label.contains(trigger, autoescape=True))
        return stmt.order_by(
            (QaEvalRun.status == RunStatus.queued).desc(),
            QaEvalRun.run_at.desc().nulls_first(),
            QaEvalRun.code.desc(),
        )

    async def fail_counts(self, run_ids: list[int]) -> dict[int, int]:
        if not run_ids:
            return {}
        stmt = (
            select(QaEvalFail.run_id, func.count())
            .where(QaEvalFail.run_id.in_(run_ids))
            .group_by(QaEvalFail.run_id)
        )
        return {rid: int(n) for rid, n in (await self.session.execute(stmt)).all()}

    def fails_query(self, run_pk: int) -> Select:
        return select(QaEvalFail).where(QaEvalFail.run_id == run_pk).order_by(QaEvalFail.id)

    async def kind_stats(self, run_pk: int) -> list[tuple]:
        stmt = (
            select(QaEvalFail.kind, func.count(), func.min(QaEvalFail.id).label("first"))
            .where(QaEvalFail.run_id == run_pk)
            .group_by(QaEvalFail.kind)
            .order_by("first")
        )
        return [(k, int(n)) for k, n, _ in (await self.session.execute(stmt)).all()]


class EvalQuestionRepo(BaseRepository[QaEvalQuestion]):
    model = QaEvalQuestion

    def list_query(self, group: str | None, last, kw: str | None) -> Select:
        """调优页只列语义计划题（kind=semplan）；sql 题是离线评测集。"""
        stmt = self.base_query().where(QaEvalQuestion.kind == "semplan")
        if group:
            stmt = stmt.where(QaEvalQuestion.group_name == group)
        if last is not None:
            stmt = stmt.where(QaEvalQuestion.last_result == last)
        if kw:
            stmt = stmt.where(QaEvalQuestion.question.contains(kw, autoescape=True))
        return stmt.order_by(QaEvalQuestion.code)

    async def group_counts(self) -> dict[str, int]:
        stmt = (
            select(QaEvalQuestion.group_name, func.count())
            .where(QaEvalQuestion.kind == "semplan")
            .group_by(QaEvalQuestion.group_name)
        )
        return {g: int(n) for g, n in (await self.session.execute(stmt)).all() if g}

    async def total(self) -> int:
        stmt = select(func.count()).where(QaEvalQuestion.kind == "semplan")
        return int(await self.session.scalar(stmt) or 0)

    async def by_crs(self, crs: list[str]) -> Sequence[QaEvalQuestion]:
        if not crs:
            return []
        stmt = self.base_query().where(QaEvalQuestion.cr_code.in_(crs)).order_by(QaEvalQuestion.code)
        return (await self.session.scalars(stmt)).all()

    async def count_cr(self, cr: str) -> int:
        return int(await self.session.scalar(select(func.count()).where(QaEvalQuestion.cr_code == cr)) or 0)

    async def by_question(self, question: str) -> QaEvalQuestion | None:
        return (
            await self.session.scalars(self.base_query().where(QaEvalQuestion.question == question))
        ).first()


class FeedbackRepo(BaseRepository[QaFeedback]):
    model = QaFeedback
    not_found_message = "反馈不存在"

    def list_query(self, kw: str | None, status, fix_kind) -> Select:
        stmt = self.base_query()
        if kw:
            stmt = stmt.where(
                or_(
                    QaFeedback.question.contains(kw, autoescape=True),
                    QaFeedback.user_name.contains(kw, autoescape=True),
                )
            )
        if status is not None:
            stmt = stmt.where(QaFeedback.status == status)
        if fix_kind is not None:
            stmt = stmt.where(QaFeedback.fix_kind == fix_kind)
        # 待处理在前；同组按编号新→旧（= mock 顺序）
        return stmt.order_by((QaFeedback.status == FbStatus.open).desc(), QaFeedback.code.desc())

    async def count_open(self) -> int:
        return int(
            await self.session.scalar(select(func.count()).where(QaFeedback.status == FbStatus.open)) or 0
        )

    async def user_codes(self, user_pks: list[int]) -> dict[int, str]:
        if not user_pks:
            return {}
        rows = await self.session.execute(select(SysUser.id, SysUser.code).where(SysUser.id.in_(user_pks)))
        return {pk: code for pk, code in rows.all()}


class FreqRepo(BaseRepository[QaFreqQuestion]):
    model = QaFreqQuestion

    def list_query(self, period: int, certified: bool | None, kw: str | None) -> tuple[Select, object]:
        hits = QaFreqQuestion.hits7 if period == 7 else QaFreqQuestion.hits30
        rank = func.row_number().over(order_by=(hits.desc(), QaFreqQuestion.code)).label("rank")
        ranked = select(QaFreqQuestion.id, rank).subquery()
        stmt = select(QaFreqQuestion, ranked.c.rank).join(ranked, ranked.c.id == QaFreqQuestion.id)
        if kw:
            stmt = stmt.where(QaFreqQuestion.question.contains(kw, autoescape=True))
        if certified is not None:
            stmt = stmt.where(
                QaFreqQuestion.cert_id.is_not(None) if certified else QaFreqQuestion.cert_id.is_(None)
            )
        return stmt.order_by(ranked.c.rank), ranked

    async def count_uncert(self) -> int:
        return int(
            await self.session.scalar(select(func.count()).where(QaFreqQuestion.cert_id.is_(None))) or 0
        )

    async def by_question(self, question: str) -> QaFreqQuestion | None:
        stmt = self.base_query().where(QaFreqQuestion.question == question)
        return (await self.session.scalars(stmt)).first()


class SynonymRepo(BaseRepository[QaSynonymLink]):
    model = QaSynonymLink

    async def all(self) -> Sequence[QaSynonymLink]:
        return (await self.session.scalars(self.base_query().order_by(QaSynonymLink.id))).all()

    async def by_word(self, word: str) -> QaSynonymLink | None:
        stmt = self.base_query().where(QaSynonymLink.word == word).order_by(QaSynonymLink.id)
        return (await self.session.scalars(stmt)).first()


class SysUserRepo(BaseRepository[SysUser]):
    model = SysUser


async def count_of(session, stmt: Select) -> int:
    return int(await session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0)
