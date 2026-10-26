"""Generate the Smart Episode Finder architecture diagram.

Uses the `diagrams` library (mingrammer/diagrams), which bundles the official AWS
service icons and renders via Graphviz.

    pip install diagrams          # also needs Graphviz on PATH
    python infra/architecture.py  # writes architecture.png

Labels are kept short. The numbered edges carry the explanation, so the diagram
reads as a sequence rather than a wall of text.
"""
from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import Lambda
from diagrams.aws.integration import StepFunctions
from diagrams.aws.management import SystemsManagerParameterStore
from diagrams.aws.ml import Bedrock, Transcribe
from diagrams.aws.network import APIGateway, CloudFront
from diagrams.aws.storage import SimpleStorageServiceS3
from diagrams.onprem.client import Users
from diagrams.onprem.network import Internet

GRAPH_ATTR = {
    "fontsize": "24",
    "bgcolor": "white",
    "pad": "0.6",
    "nodesep": "0.45",
    "ranksep": "0.95",
    "splines": "spline",
    "compound": "true",
}
NODE_ATTR = {"fontsize": "12"}
EDGE_ATTR = {"fontsize": "11"}

VIOLET = "#7c3aed"
GREEN = "#059669"
GREY = "gray50"

with Diagram(
    "Smart Episode Finder",
    filename="architecture",
    show=False,
    direction="TB",
    graph_attr=GRAPH_ATTR,
    node_attr=NODE_ATTR,
    edge_attr=EDGE_ATTR,
):
    # ---------------- front door ----------------
    user = Users("You")
    cdn = CloudFront("CloudFront\nHTTPS")
    site = SimpleStorageServiceS3("S3\nweb page")
    api = APIGateway("API Gateway\nREST API")

    user >> Edge(label="opens the site") >> cdn
    cdn >> Edge(label="page", color=GREY) >> site
    cdn >> Edge(label="/api/*") >> api

    # ---------------- path 1 : titles only ----------------
    with Cluster("1  Rank a channel's videos   —   reads titles, never hears them"):
        api_fn = Lambda("spf-api\nstarts the job")
        flow = StepFunctions("Step Functions\n4 stages, auto-retry")
        acts = Lambda("spf-activities\nsearch + score")
        yt = Internet("YouTube Data API\ntitles, tags, views")
        rank_llm = Bedrock("Bedrock\nLlama 3.3 70B")

        api_fn >> Edge(label="1  run pipeline", color=VIOLET) >> flow
        flow >> Edge(label="2  do the work", color=VIOLET) >> acts
        acts >> Edge(label="3  get video info", color=VIOLET) >> yt
        acts >> Edge(label="4  score 0-100", color=VIOLET) >> rank_llm

    # ---------------- path 2 : the agent ----------------
    with Cluster("2  The agent   —   picks its own tools"):
        agent = Lambda("spf-agent\nStrands agent")
        brain = Bedrock("Bedrock\nNova 2 Lite")

        with Cluster("NEW TOOL  ·  listen_to_episode"):
            rss = Internet("Podcast RSS\nreal MP3 audio")
            audio = SimpleStorageServiceS3("S3\naudio excerpt")
            stt = Transcribe("Amazon Transcribe\nspeech to text")

            rss >> Edge(label="3  grab 10 min", color=GREEN) >> audio
            audio >> Edge(label="4  transcribe", color=GREEN) >> stt

        agent >> Edge(label="1  which tool?", color=GREEN) >> brain
        agent >> Edge(label="2  find the episode", color=GREEN) >> rss
        stt >> Edge(label="5  quotes + timestamps", color=GREEN, style="dashed") >> brain

    api >> Edge(label="analyse") >> api_fn
    api >> Edge(label="ask", color=GREEN) >> agent

    # ---------------- shared ----------------
    with Cluster("Shared"):
        jobs = SimpleStorageServiceS3("S3\njob state + results")
        secrets = SystemsManagerParameterStore("Parameter Store\nYouTube key, encrypted")

    acts >> Edge(label="5  save", style="dashed", color=GREY) >> jobs
    agent >> Edge(label="6  save", style="dashed", color=GREY) >> jobs
    acts >> Edge(style="dashed", color=GREY) >> secrets
    agent >> Edge(style="dashed", color=GREY) >> secrets

print("wrote architecture.png")
