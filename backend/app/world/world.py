"""World: locations, who is where, and Level-0 action execution.

Everything here is pure rules -- movement, energy, sleep, work.
The world never calls an LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..agents.agent import Agent
from ..agents.core import MemoryItem

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

# A landmark's creator advancing it through one work decision: progress climbs by
# duration / this. At 1200 a full unit is ~20 work-hours, so finishing the seeded
# half-mural takes ~10 hours of Emma's brush.
LANDMARK_WORK_DIVISOR = 1200

# How the pure-rules observation layer narrates a neighbour's current action.
_ACTIVITY_VERB = {
    "work": "working", "rest": "resting", "eat": "eating",
    "talk": "talking", "idle": "idling", "move": "heading out",
}


@dataclass
class Location:
    id: str
    name: str
    kind: str  # home | cafe | office | park | market
    x: float = 0.0   # map coordinates for the UI
    y: float = 0.0
    owner: str = ""          # agent_id who runs this place ("" = public/unowned)
    price: float = 0.0       # cost of one meal here (0 = free, e.g. eating at home)
    revenue: float = 0.0     # lifetime takings
    revenue_today: float = 0.0  # takings since the last day boundary (reset in settlement)
    # World objects that live here. Each: {"id", "name", "state": in_progress|completed,
    # "progress": 0.0-1.0, "created_by": agent_id}. Pure Level-0 state; the creator's
    # `work` here nudges progress (see advance_landmark).
    landmarks: list[dict] = field(default_factory=list)


@dataclass
class Observation:
    location: str
    nearby_agents: list[str]          # agent ids at the same location
    arrivals: list[str]               # ids that arrived since agent's last decision
    activities: list[str] = field(default_factory=list)  # short English lines: what others here are doing

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
        # World effects (weather / festivals). Pure Level-0 state; no LLM.
        # Each: {"type": "rain"|"festival", "location": str, "until_minute": int}.
        self.active_effects: list[dict] = []

    # ---- world effects (rain / festival) ----------------------------

    def effect_active(self, type: str) -> dict | None:
        for eff in self.active_effects:
            if eff["type"] == type:
                return eff
        return None

    def set_effect(self, type: str, location: str, until_minute: int) -> bool:
        """Add a world effect, or extend an existing one of the same type
        (effects never stack -- a repeat just pushes ``until_minute`` out and
        refreshes the location). Returns True only on a fresh activation."""
        eff = self.effect_active(type)
        if eff is not None:
            eff["until_minute"] = max(eff["until_minute"], until_minute)
            if location:
                eff["location"] = location
            return False
        self.active_effects.append(
            {"type": type, "location": location, "until_minute": until_minute}
        )
        return True

    def expire_effects(self, now: int) -> list[dict]:
        """Drop effects whose window has closed and return them, so the caller
        (the engine) can publish the matching end events."""
        expired = [e for e in self.active_effects if e["until_minute"] <= now]
        if expired:
            self.active_effects = [e for e in self.active_effects if e["until_minute"] > now]
        return expired

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
        landmarks = self.locations[loc].landmarks if loc in self.locations else []
        activities = [self._describe_activity(self.agents[aid], landmarks) for aid in nearby]
        return Observation(location=loc, nearby_agents=nearby, arrivals=arrivals, activities=activities)

    def _describe_activity(self, other: Agent, landmarks: list[dict]) -> str:
        """One short English line for what ``other`` is doing right here -- and, if
        they're the creator working on a landmark, how far along it is."""
        act = other.state.current_action
        lm = next(
            (l for l in landmarks
             if l.get("created_by") == other.id and l.get("state") == "in_progress"),
            None,
        )
        if act == "work" and lm is not None:
            return f"{other.name} is working on {lm['name']} ({int(lm['progress'] * 100)}% done)"
        return f"{other.name} is {_ACTIVITY_VERB.get(act, act)}"

    # ---- landmarks (Level 0, pure rules) ----------------------------

    def advance_landmark(self, agent: Agent, duration: int, now: int) -> dict | None:
        """The creator worked here: nudge their in-progress landmark's progress.
        On completion, flip its state, reward the creator with a high-importance
        memory, and ripple a memory out to everyone who ever saw it under way.
        Returns a ``landmark_done`` event descriptor on completion, else None."""
        loc = self.locations.get(agent.state.location)
        if loc is None:
            return None
        lm = next(
            (l for l in loc.landmarks
             if l.get("created_by") == agent.id and l.get("state") == "in_progress"),
            None,
        )
        if lm is None:
            return None
        lm["progress"] = min(1.0, lm["progress"] + duration / LANDMARK_WORK_DIVISOR)
        if lm["progress"] < 1.0:
            return None
        lm["state"] = "completed"
        bare = lm["name"][4:] if lm["name"].startswith("the ") else lm["name"]
        agent.memory.add(MemoryItem(
            minute=now, importance=8,
            text=f"I finished {lm['name']} at {loc.name}. It's done at last.",
        ))
        # The ripple: everyone who watched it grow shares the moment it's complete.
        for other in self.agents.values():
            if other.id != agent.id and lm["id"] in other.state.seen_landmark_progress:
                other.memory.add(MemoryItem(
                    minute=now, importance=4,
                    text=f"{agent.name}'s {bare} at {loc.name} is finished. It looks wonderful.",
                ))
        return {"verb": "landmark_done", "location": loc.id, "text": lm["name"]}

    # ---- execution (Level 0, pure rules) ----------------------------

    def execute(
        self, agent: Agent, action: str, target_location: str | None, now: int, duration: int
    ) -> dict | None:
        """Apply an action's world effects.

        Returns a structured result {"verb": ..., "location": location_id}
        for the engine to publish as an event, or None when no event should
        be emitted (e.g. still asleep). Rendering to any human language is
        NOT this layer's job.
        """
        st = agent.state

        if action == "move" and target_location and target_location != st.location:
            st.location = target_location
            st.current_action = "move"
            self._recent_arrivals[target_location].append((now, agent.id))
            # Trim arrival logs so they don't grow forever.
            self._recent_arrivals[target_location] = self._recent_arrivals[target_location][-20:]
            # Rain makes the walk 1.5x as tiring (pure rule -- energy only, not travel time).
            travel = int(TRAVEL_MINUTES * 1.5) if self.effect_active("rain") else TRAVEL_MINUTES
            self._apply_energy(agent, "move", travel)
            return {"verb": "arrive", "location": target_location}

        # Eating somewhere with an owner + price = a paid transaction (Level 0, no LLM).
        # Charge at most once per meal slot: a re-eat after an interruption within
        # the same routine slot just eats (free), it doesn't ring up a second sale.
        if action == "eat":
            loc = self.locations.get(st.location)
            if loc and loc.owner and loc.price > 0 and loc.owner != agent.id:
                slot = (now // (24 * 60)) * (24 * 60) + agent.routine.current(now % (24 * 60)).start
                if st.last_meal_slot == slot:
                    pass  # already settled this meal slot -> fall through to a free eat
                elif st.money >= loc.price:
                    st.last_meal_slot = slot
                    st.money -= loc.price
                    owner = self.agents.get(loc.owner)
                    if owner is not None:
                        owner.state.money += loc.price
                    loc.revenue += loc.price
                    loc.revenue_today += loc.price
                    st.meals_bought += 1
                    if st.meals_bought == 1 or st.meals_bought % 5 == 0:  # throttle the memory
                        agent.memory.add(MemoryItem(
                            minute=now, text=f"Had a meal at {loc.name}.", importance=1))
                else:
                    # Too poor to buy a meal -> fall back to resting, and remember the sting.
                    st.last_meal_slot = slot   # settle the slot so we don't re-broke on re-entry
                    agent.memory.add(MemoryItem(
                        minute=now, text=f"Couldn't afford to eat at {loc.name}.", importance=5))
                    st.current_action = "rest"
                    self._apply_energy(agent, "rest", duration)
                    return {"verb": "broke", "location": st.location}

        already_sleeping = action == "sleep" and st.current_action == "sleep"
        st.current_action = action
        self._apply_energy(agent, action, duration)
        if already_sleeping:
            return None  # no duplicate "went to sleep" events
        return {"verb": action, "location": st.location}

    @staticmethod
    def _apply_energy(agent: Agent, action: str, minutes: int) -> None:
        delta = ENERGY_PER_HOUR.get(action, 0) * minutes / 60
        agent.state.energy = int(max(0, min(100, agent.state.energy + delta)))
