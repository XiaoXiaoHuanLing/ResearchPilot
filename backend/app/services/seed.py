from sqlalchemy.orm import Session

from app.db.models import ArticleModel, ReportModel, TopicModel


def seed_demo_data(db: Session) -> None:
    if db.query(TopicModel).first() is None:
        db.add_all([
            TopicModel(
                name="舰船动态追踪",
                description="跟踪公开来源中的舰船与海军装备动态",
                keywords="驱逐舰,护卫舰,舰船,海军",
                schedule="每天 08:00 / 20:00",
                enabled=True,
            ),
            TopicModel(
                name="武器装备前沿",
                description="跟踪导弹、雷达、无人装备等公开资讯",
                keywords="导弹,雷达,无人机,装备",
                schedule="每天 09:00",
                enabled=True,
            ),
            TopicModel(
                name="无人系统跟踪",
                description="跟踪无人机、无人艇、无人车等公开资讯",
                keywords="无人机,无人艇,无人车,自主系统",
                schedule="每天 09:00 / 21:00",
                enabled=True,
            ),
        ])

    if db.query(ArticleModel).first() is None:
        db.add_all([
            ArticleModel(
                topic="舰船动态追踪",
                title="某型驱逐舰公开项目进展汇总",
                source="公开防务资讯站",
                published_at="2026-04-08 18:30",
                summary="系统从公开报道中提取项目进展、节点时间与相关技术线索，供后续研究比对。",
                url="https://example.com/article-1",
                bookmarked=True,
                content="根据公开报道，某型驱逐舰项目已进入新阶段。多个公开渠道披露了项目关键节点时间表，"
                        "包括设计定型、系泊试验和航行试验等阶段。技术方面，公开信息提及了综合射频系统、"
                        "垂直发射系统和综合电力推进等关键技术方向。该项目预计将在未来数年内持续进展，"
                        "相关供应链和配套体系建设也在同步推进中。值得注意的是，公开信息中多次提及"
                        "模块化设计理念在舰船建造中的应用，这可能对后续维护和升级产生重要影响。",
            ),
            ArticleModel(
                topic="武器装备前沿",
                title="新型无人装备公开信息整理",
                source="公开技术新闻",
                published_at="2026-04-08 16:10",
                summary="对近期公开技术动态进行整理，提取关键能力描述与应用方向。",
                url="https://example.com/article-2",
                bookmarked=False,
                content="近期公开技术报道集中关注无人装备领域的发展动态。多个公开渠道披露了新型无人系统的"
                        "技术特征和部署方向，包括长航时无人侦察平台、小型化察打一体系统以及集群控制技术的"
                        "进展。在应用层面，公开信息提到了无人系统在边境巡逻、海上监测和应急响应等场景的"
                        "部署计划。技术路线方面，人工智能自主决策、多平台协同和数据链融合成为公开报道中"
                        "频繁出现的关键词。",
            ),
            ArticleModel(
                topic="无人系统跟踪",
                title="无人艇海上测试公开进展",
                source="海洋技术观察",
                published_at="2026-04-08 14:20",
                summary="公开渠道报道了无人艇海上测试的最新阶段，涉及自主航行和编队协同能力。",
                url="https://example.com/article-3",
                bookmarked=True,
                content="公开报道显示，无人艇海上测试已进入新阶段。多个测试批次在公开海域完成了自主航行、"
                        "障碍规避和编队协同等科目的验证。技术方面，公开信息提到了基于视觉和雷达融合的"
                        "环境感知系统、分布式编队控制算法以及海况自适应航迹规划等技术要点。测试结果"
                        "表明，在4级海况下系统仍能保持稳定的编队队形和任务执行能力。后续测试计划将"
                        "扩展到更复杂的协同场景，包括多艇联合探测和自主决策任务分配。",
            ),
        ])

    if db.query(ReportModel).first() is None:
        db.add(
            ReportModel(
                title="舰船动态周报（示例）",
                created_at="2026-04-08 21:00",
                summary="汇总近期公开舰船动态并生成结构化观察结论。信息来源：公开防务资讯站。",
            )
        )

    db.commit()
