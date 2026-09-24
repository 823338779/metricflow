from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from metricflow_semantics.specs.linkable_spec_set import LinkableSpecSet

from metricflow.dataflow.builder.node_evaluator import JoinLinkableInstancesRecipe
from metricflow.dataflow.dataflow_plan import DataflowPlanNode
from metricflow.dataflow.nodes.join_to_base import JoinDescription


@dataclass(frozen=True)
class SourceNodeRecipe:
    """Get a recipe for how to build a dataflow plan node that outputs simple-metric inputs and linkable instances as needed.

    源节点选择结果：从哪个分支取指标，以及为取得所需维度还要做哪些 JOIN。
    """

    # 被选为左侧起点的分支，必须能提供简单指标输入；后续 JOIN、筛列与聚合从这里向上构建。
    source_node: DataflowPlanNode
    # 即使不是最终输出，也必须从左侧源保留的项，例如关联右侧维度时要用的实体键；
    # SelectorNode 若过早丢弃它们，后续 JOIN 就无法执行。
    required_local_linkable_specs: LinkableSpecSet
    # 缺失分组项的补齐方案，指定右侧来源和连接条件；join_targets 将它转成 JoinOnEntitiesNode 的输入。
    join_linkable_instances_recipes: Tuple[JoinLinkableInstancesRecipe, ...]
    # 左右来源合起来必须提供的项；后续构建步骤以此判断哪些时间维度尚未具备、是否还需变换。
    all_linkable_specs_required_for_source_nodes: LinkableSpecSet

    @property
    def join_targets(self) -> List[JoinDescription]:
        """Joins to be made to source node."""
        return [join_recipe.join_description for join_recipe in self.join_linkable_instances_recipes]
