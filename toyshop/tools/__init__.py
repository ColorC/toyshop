"""ToyShop Tools - OpenSpec workflow tools using openhands-sdk.

Each tool represents a step in the development workflow:
- AnalyzeInputTool: Parse user requirements
- GenerateProposalTool: Create OpenSpec proposal
- DesignModulesTool: Design system architecture
- DesignInterfacesTool: Define interfaces
- GenerateTasksTool: Break down into tasks
- GenerateSpecTool: Create test scenarios
"""

from toyshop.tools.analyze_input import AnalyzeInputTool
from toyshop.tools.generate_proposal import GenerateProposalTool
from toyshop.tools.design_modules import DesignModulesTool
from toyshop.tools.design_interfaces import DesignInterfacesTool
from toyshop.tools.generate_tasks import GenerateTasksTool
from toyshop.tools.generate_spec import GenerateSpecTool

__all__ = [
    "AnalyzeInputTool",
    "GenerateProposalTool",
    "DesignModulesTool",
    "DesignInterfacesTool",
    "GenerateTasksTool",
    "GenerateSpecTool",
]
