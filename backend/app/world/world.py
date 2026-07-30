"""World: locations, who is where, and Level-0 action execution.

Everything here is pure rules -- movement, energy, sleep, work.
The world never calls an LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..agents.agent import Agent

# Energy delta per hour of doing an action (scaled by duration).
ENERGY_PER_HOUR = {
    "sleep": +12,
    "rest": +8,
    "eat": +5,
    "work": -6,
    "move": -2,
    "talk": -1,
    "idle": -1,
}

# Travel time between any two locations (minutes). Flat for phase 1;
# becomes real pathfinding when the map exists.
TRAVEL_MINUTES = 10


@dataclass
class Location:
    id: str
    name: str
    kind: str  # home | cafe | office | park | market
    x: float = 0.0   # map coordinates for the UI
    y: float = 0.0


@dataclass
class Observation:
    location: str
    nearby_agents: list[str]          # agent ids at the same location
    arrivals: list[str]               # ids that arrived since agent's last decision

    def describe(self, world: "World") -> str:
        if not self.nearby_agents:
            return f"Alone at {self.location}."
        names = ", ".join(world.agents[a].name for a in self.nearby_agents)
        return f"At {self.location} with {names}."


class World:
    def __init__(self, locations: list[Location], agents: list[Agent]):
        self.locations: dict[str, Location] = {l.id: l for l in locations}
        self.agents: dict[str, Agent] = {a.id: a for a in agents}
        self._recent_arrivals: dict[str, list[tuple[int, str]]] = {
            lid: [] for lid in self.locations
        }  # location -> [(minute, agent_id)]

    # ---- observation ------------------------------------------------

    def observe(self, agent: Agent, since_minute: int, now: int) -> Observation:
        loc = agent.state.location
        nearby = [
            a.id
            for a in self.agents.values()
            if a.id != agent.id
            and a.state.location == loc
            and a.state.current_action != "sleep"
        ]
        arrivals = [
            aid
            for (minute, aid) in self._recent_arrivals.get(loc, [])
            if minute > since_minute and aid != agent.id and aid in nearby
        ]
        return Observation(location=loc, nearby_agents=nearby, arrivals=arrivals)

    # ---- execution (Level 0, pure rules) ----------------------------

    def execute(self, agent: Agent, action: str, target_location: str | None, now: int, duration: int) -> str:
        """Apply an action's world effects. Returns a human-readable event text."""
        st = agent.state

        if action == "move" and target_location and target_location != st.location:
            st.location = target_location
            st.current_action = "move"
            self._recent_arrivals[target_location].append((now, agent.id))
            # Trim arrival logs so they don't grow forever.
            self._recent_arrivals[target_location] = self._recent_arrivals[target_location][-20:]
            self._apply_energy(agent, "move", TRAVEL_MINUTES)
            return f"{agent.name} → {self.locations[target_location].name}"

        already_sleeping = action == "sleep" and st.current_action == "sleep"
        st.current_action = action
        self._apply_energy(agent, action, duration)
        if already_sleeping:
            return ""  # no duplicate "went to sleep" events
        loc_name = self.locations[st.location].name
        verbs = {
            "work": "is working at",
            "rest": "is resting at",
            "eat": "is eating at",
            "sleep": "went to sleep at",
            "idle": "is idling at",
        }
        return f"{agent.name} {verbs.get(action, action)} {loc_name}"

    @staticmethod
    def _apply_energy(agent: Agent, action: str, minutes: int) -> None:
        delta = ENERGY_PER_HOUR.get(action, 0) * minutes / 60
        agent.state.energy = int(max(0, min(100, agent.state.energy + delta)))
