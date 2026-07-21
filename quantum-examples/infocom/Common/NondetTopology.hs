{- |
    Shared policies and network configuration for the non-deterministic
    butterfly-topology experiments in the paper.
-}

module Common.NondetTopology
    ( ProtocolDirection(..)
    , defaultProtocolName
    , availableProtocols
    , selectProtocol
    , protocolPolicy
    , leftToRightProtocol
    , rightToLeftProtocol
    , protocolBounds
    , missingAnyGoal
    , selectEvent
    , selectLoopTest
    , generationLinks
    , actionConfigFor
    ) where

import BellKAT.QuantumPrelude hiding (lookup)
import qualified Common.NetworkConfig as Net
import Data.List (intercalate)

data ProtocolDirection
    = LeftToRight
    | RightToLeft
    deriving stock (Eq, Show)

defaultProtocolName :: String
defaultProtocolName = "left-to-right"

missingAnyGoal :: QBKATTest
missingAnyGoal =
        "A" /~? "C"
    &&* "B" /~? "D"

generations :: QBKATPolicy
generations =
        ucreate ("A", "X")
    <||>
        ucreate ("B", "X")
    <||>
        ucreate ("X", "Y")
    <||>
        ucreate ("C", "Y")
    <||>
        ucreate ("D", "Y")

leftAGuard :: QBKATTest
leftAGuard = hasSubset ["A" ~ "X", "X" ~ "Y"] &&* "A" /~? "Y" &&* "A" /~? "C"

leftBGuard :: QBKATTest
leftBGuard = hasSubset ["B" ~ "X", "X" ~ "Y"] &&* "B" /~? "Y" &&* "B" /~? "D"

leftGoalACGuard :: QBKATTest
leftGoalACGuard = hasSubset ["A" ~ "Y", "C" ~ "Y"] &&* "A" /~? "C"

leftGoalBDGuard :: QBKATTest
leftGoalBDGuard = hasSubset ["B" ~ "Y", "D" ~ "Y"] &&* "B" /~? "D"

chooseLeftBranch :: QBKATPolicy
chooseLeftBranch =
        ite leftAGuard
            (swap "X" ("A", "Y"))
            mempty
    <||>
        ite leftBGuard
            (swap "X" ("B", "Y"))
            mempty

chooseRightEndpoint :: QBKATPolicy
chooseRightEndpoint =
        ite leftGoalACGuard
            (swap "Y" ("A", "C"))
            mempty
    <||>
        ite leftGoalBDGuard
            (swap "Y" ("B", "D"))
            mempty

-- | The policy used by @P_compare_nondet_goals.hs@: resolve the shared
-- X-Y resource at X, then complete the enabled goal at Y.
leftToRightProtocol :: QBKATTest -> QBKATPolicy
leftToRightProtocol loopGuard =
    while loopGuard
        ( generations
        <>
          chooseLeftBranch
        <>
          chooseRightEndpoint
        )

rightCGuard :: QBKATTest
rightCGuard = hasSubset ["X" ~ "Y", "C" ~ "Y"] &&* "X" /~? "C" &&* "A" /~? "C"

rightDGuard :: QBKATTest
rightDGuard = hasSubset ["X" ~ "Y", "D" ~ "Y"] &&* "X" /~? "D" &&* "B" /~? "D"

rightGoalACGuard :: QBKATTest
rightGoalACGuard = hasSubset ["A" ~ "X", "X" ~ "C"] &&* "A" /~? "C"

rightGoalBDGuard :: QBKATTest
rightGoalBDGuard = hasSubset ["B" ~ "X", "X" ~ "D"] &&* "B" /~? "D"

chooseRightBranch :: QBKATPolicy
chooseRightBranch =
        ite rightCGuard
            (swap "Y" ("X", "C"))
            mempty
    <||>
        ite rightDGuard
            (swap "Y" ("X", "D"))
            mempty

chooseLeftEndpoint :: QBKATPolicy
chooseLeftEndpoint =
        ite rightGoalACGuard
            (swap "X" ("A", "C"))
            mempty
    <||>
        ite rightGoalBDGuard
            (swap "X" ("B", "D"))
            mempty

-- | Mirror the swapping order: resolve X-Y at Y first, then complete the
-- matching goal at X.
rightToLeftProtocol :: QBKATTest -> QBKATPolicy
rightToLeftProtocol loopGuard =
    while loopGuard
        ( generations
        <>
          chooseRightBranch
        <>
          chooseLeftEndpoint
        )

protocols :: [(String, ProtocolDirection)]
protocols =
    [ ("left-to-right", LeftToRight)
    , ("right-to-left", RightToLeft)
    ]

availableProtocols :: String
availableProtocols = intercalate ", " (fmap fst protocols)

selectProtocol :: String -> Either String ProtocolDirection
selectProtocol name =
    maybe
        (Left $ "Unknown protocol '" <> name <> "'. Available protocols: " <> availableProtocols)
        Right
        (lookup name protocols)

protocolPolicy :: ProtocolDirection -> QBKATTest -> QBKATPolicy
protocolPolicy direction =
    case direction of
        LeftToRight -> leftToRightProtocol
        RightToLeft -> rightToLeftProtocol

events :: [(String, QBKATTest)]
events =
    [ ("a-c", "A" ~~? "C")
    , ("b-d", "B" ~~? "D")
    , ("either", "A" ~~? "C" ||* "B" ~~? "D")
    , ("a-c-or-b-d", "A" ~~? "C" ||* "B" ~~? "D")
    ]

availableEvents :: String
availableEvents = intercalate ", " (fmap fst events)

selectEvent :: String -> Either String QBKATTest
selectEvent name =
    maybe
        (Left $ "Unknown event '" <> name <> "'. Available events: " <> availableEvents)
        Right
        (lookup name events)

loopTests :: [(String, QBKATTest)]
loopTests =
    [ ("a-c", "A" /~? "C")
    , ("b-d", "B" /~? "D")
    , ("either", missingAnyGoal)
    , ("a-c-or-b-d", missingAnyGoal)
    ]

selectLoopTest :: String -> Either String QBKATTest
selectLoopTest name =
    maybe
        (Left $ "Unknown loop test '" <> name <> "'. Available events: " <> availableEvents)
        Right
        (lookup name loopTests)

generationLinks :: [(Location, Location)]
generationLinks =
    [ ("A", "X")
    , ("B", "X")
    , ("X", "Y")
    , ("C", "Y")
    , ("D", "Y")
    ]

leftToRightCapacityPairs :: [(Location, Location)]
leftToRightCapacityPairs =
    generationLinks <>
    [ ("A", "Y")
    , ("B", "Y")
    , ("A", "C")
    , ("B", "D")
    ]

rightToLeftCapacityPairs :: [(Location, Location)]
rightToLeftCapacityPairs =
    generationLinks <>
    [ ("X", "C")
    , ("X", "D")
    , ("A", "C")
    , ("B", "D")
    ]

protocolBounds :: ProtocolDirection -> NetworkBounds QBKATTag
protocolBounds direction =
    Net.networkBoundsFor $
        case direction of
            LeftToRight -> leftToRightCapacityPairs
            RightToLeft -> rightToLeftCapacityPairs

actionConfigFor :: Net.NetworkParameters -> ProbabilisticActionConfiguration
actionConfigFor parameters =
    Net.actionConfigFor parameters generationLinks ["X", "Y"]
