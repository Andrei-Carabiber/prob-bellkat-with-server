{-# LANGUAGE DerivingStrategies #-}
{-# LANGUAGE OverloadedStrings #-}
{-# LANGUAGE UndecidableInstances #-}
module BellKAT.Implementations.MDPExtremal
    ( ConcreteMDPState
    , ExtremalQuery(..)
    , CoverageStatus(..)
    , SchedulerMetadata(..)
    , SchedulerExtremum(..)
    , SchedulerObjective(..)
    , SchedulerArtifact
    , ScheduledResult(..)
    , SchedulerChoiceTrace(..)
    , SchedulerChoice(..)
    , SchedulerSelection(..)
    , ExtremalResult(..)
    , computeExtremalReachability
    , computeExtremalReachabilityWithMetadata
    , computeScheduledReachability
    , extremalDPTablesToJSON
    , scheduledResultToJSON
    , scheduledOccupancyToJSON
    , renderExtremalResult
    , renderExtremalDPTables
    , renderScheduledResult
    ) where

import qualified Data.Aeson                  as A
import qualified Data.Aeson.KeyMap           as AKM
import           Control.Monad               (foldM)
import           Data.Aeson                  ((.:), (.:?), (.!=))
import           Data.Bits                   (xor)
import           Data.List                   (foldl', intercalate, mapAccumL, transpose, zipWith5)
import           Data.Maybe                  (fromMaybe)
import           Data.Monoid                 (Sum (..))
import qualified Data.IntMap.Strict          as IM
import qualified Data.Map.Strict             as Map
import qualified Data.Set                    as Set
import           Data.Word                   (Word64)
import           GHC.Exts                    (IsList, Item, toList)
import           Numeric                     (showFFloat, showHex)

import           BellKAT.Utils.MDP
    ( MDP(..)
    , StepCost(..)
    )
import           BellKAT.Utils.Automata.Transitions.Functorial (StateSystem(..))
import           BellKAT.Utils.Convex        (getGenerators)
import           BellKAT.Utils.Distribution  (D, RationalOrDouble, toDouble)
import qualified BellKAT.Utils.Distribution  as D

type ConcreteMDPState s = (Int, s)

type ExtremalTable s p = Map.Map (ConcreteMDPState s) (IM.IntMap p)

type Action s p = D p (ConcreteMDPState s, StepCost)

type SchedulerChoiceLog s p = Map.Map (ConcreteMDPState s) [SchedulerChoice s p]

type ScheduledOccupancy s p = Map.Map (ConcreteMDPState s) (IM.IntMap p)

data SchedulerMetadata = SchedulerMetadata
    { smSemantics :: Maybe String
    , smObjectiveEvent :: Maybe String
    }
    deriving stock (Eq, Show)

data SchedulerExtremum = SchedulerMin | SchedulerMax
    deriving stock (Eq, Ord, Show)

data SchedulerObjective = SchedulerObjective
    { soEvent :: Maybe String
    , soExtremum :: SchedulerExtremum
    , soResolvedBudget :: Int
    }
    deriving stock (Eq, Show)

data SchedulerStateEntry = SchedulerStateEntry
    { sseStateId :: Int
    , ssePC :: Int
    , sseBellPairs :: [String]
    , sseRendered :: String
    }
    deriving stock (Eq, Show)

data SchedulerActionEntry = SchedulerActionEntry
    { saeStateId :: Int
    , saeActionIndex :: Int
    , saeActionDigest :: String
    , saeRendered :: String
    }
    deriving stock (Eq, Show)

data SchedulerTraceChange = SchedulerTraceChange
    { stcFromBudget :: Int
    , stcActionIndex :: Int
    , stcActionDigest :: String
    , stcTie :: Bool
    , stcValue :: Maybe Double
    }
    deriving stock (Eq, Show)

data SchedulerTraceEntry = SchedulerTraceEntry
    { steStateId :: Int
    , steChanges :: [SchedulerTraceChange]
    }
    deriving stock (Eq, Show)

data SchedulerArtifact = SchedulerArtifact
    { saVersion :: Int
    , saSemantics :: Maybe String
    , saObjective :: SchedulerObjective
    , saMDPFingerprint :: String
    , saStates :: [SchedulerStateEntry]
    , saActions :: [SchedulerActionEntry]
    , saTraces :: [SchedulerTraceEntry]
    }
    deriving stock (Eq, Show)

-- | Compact scheduler trace for one state.
--
-- The trace only stores change points: each 'SchedulerChoice' applies from its
-- budget until the next listed change point for the same state.
data SchedulerChoiceTrace s p = SchedulerChoiceTrace
    { sctState :: ConcreteMDPState s
    , sctChanges :: [SchedulerChoice s p]
    }
    deriving stock (Eq, Show)

-- | Scheduler choice beginning at a particular budget.
data SchedulerChoice s p = SchedulerChoice
    { scBudget :: Int
    , scSelection :: SchedulerSelection s p
    }
    deriving stock (Eq, Show)

data SchedulerSelection s p
    = ChosenAction Int p [(Int, p)] (Action s p)
    | AllActionsSameValue Int p [(Int, p)] (Action s p)
    deriving stock (Eq, Show)

data BudgetCell s p = BudgetCell
    { bcValue :: p
    , bcChoice :: Maybe (ConcreteMDPState s, SchedulerChoice s p)
    }

data ExtremalQuery
    = ExtremalBudget Int
    | ExtremalCoverage Double
    deriving stock (Eq, Show)

data CoverageStatus p
    = CoverageReached
        { coverageTarget :: Double
        , coverageBudget :: Int
        , coverageValue :: p
        }
    | CoverageUnreachable
        { coverageTarget :: Double
        , coverageBudget :: Int
        , coverageValue :: p
        }
    deriving stock (Eq, Show)

data ExtremalResult s p = ExtremalResult
    { erInitialState :: ConcreteMDPState s
    , erStates :: [ConcreteMDPState s]
    , erGoalStates :: [ConcreteMDPState s]
    , erResolvedBudget :: Int
    , erMDPFingerprint :: String
    , erMinTable :: ExtremalTable s p
    , erMaxTable :: ExtremalTable s p
    , erMinSchedulerChoices :: [SchedulerChoiceTrace s p]
    , erMaxSchedulerChoices :: [SchedulerChoiceTrace s p]
    , erMinScheduler :: SchedulerArtifact
    , erMaxScheduler :: SchedulerArtifact
    , erCoverageStatus :: Maybe (CoverageStatus p)
    }
    deriving stock (Eq, Show)

data ScheduledResult s p = ScheduledResult
    { srInitialState :: ConcreteMDPState s
    , srStates :: [ConcreteMDPState s]
    , srGoalStates :: [ConcreteMDPState s]
    , srResolvedBudget :: Int
    , srSchedulerHorizon :: Int
    , srMDPFingerprint :: String
    , srPMFSeries :: [p]
    , srCDFSeries :: [p]
    , srOccupancy :: ScheduledOccupancy s p
    , srSchedulerObjective :: SchedulerObjective
    }
    deriving stock (Eq, Show)

data ScheduledReplay s p = ScheduledReplay
    { srepPMFSeries :: [p]
    , srepCDFSeries :: [p]
    , srepOccupancy :: ScheduledOccupancy s p
    }
    deriving stock (Eq, Show)

instance A.ToJSON SchedulerExtremum where
    toJSON = A.toJSON . schedulerExtremumName

instance A.FromJSON SchedulerExtremum where
    parseJSON value = do
        name <- A.parseJSON value
        case name :: String of
            "min" -> pure SchedulerMin
            "max" -> pure SchedulerMax
            other -> fail $ "Unknown scheduler extremum '" <> other <> "'."

instance A.ToJSON SchedulerObjective where
    toJSON objective =
        A.object
            [ "event" A..= soEvent objective
            , "extremum" A..= soExtremum objective
            , "resolved_budget" A..= soResolvedBudget objective
            ]

instance A.FromJSON SchedulerObjective where
    parseJSON =
        A.withObject "SchedulerObjective" $ \o ->
            SchedulerObjective
                <$> o .:? "event"
                <*> o .: "extremum"
                <*> o .: "resolved_budget"

instance A.ToJSON SchedulerStateEntry where
    toJSON stateEntry =
        A.object
            [ "state_id" A..= sseStateId stateEntry
            , "pc" A..= ssePC stateEntry
            , "bell_pairs" A..= sseBellPairs stateEntry
            , "rendered" A..= sseRendered stateEntry
            ]

instance A.FromJSON SchedulerStateEntry where
    parseJSON =
        A.withObject "SchedulerStateEntry" $ \o ->
            SchedulerStateEntry
                <$> o .: "state_id"
                <*> o .: "pc"
                <*> (o .:? "bell_pairs" .!= [])
                <*> (o .:? "rendered" .!= "")

instance A.ToJSON SchedulerActionEntry where
    toJSON actionEntry =
        A.object
            [ "state_id" A..= saeStateId actionEntry
            , "action_index" A..= saeActionIndex actionEntry
            , "action_digest" A..= saeActionDigest actionEntry
            , "rendered" A..= saeRendered actionEntry
            ]

instance A.FromJSON SchedulerActionEntry where
    parseJSON =
        A.withObject "SchedulerActionEntry" $ \o ->
            SchedulerActionEntry
                <$> o .: "state_id"
                <*> o .: "action_index"
                <*> o .: "action_digest"
                <*> (o .:? "rendered" .!= "")

instance A.ToJSON SchedulerTraceChange where
    toJSON change =
        A.object
            [ "from_budget" A..= stcFromBudget change
            , "action_index" A..= stcActionIndex change
            , "action_digest" A..= stcActionDigest change
            , "tie" A..= stcTie change
            , "value" A..= stcValue change
            ]

instance A.FromJSON SchedulerTraceChange where
    parseJSON =
        A.withObject "SchedulerTraceChange" $ \o ->
            SchedulerTraceChange
                <$> o .: "from_budget"
                <*> o .: "action_index"
                <*> o .: "action_digest"
                <*> (o .:? "tie" .!= False)
                <*> o .:? "value"

instance A.ToJSON SchedulerTraceEntry where
    toJSON traceEntry =
        A.object
            [ "state_id" A..= steStateId traceEntry
            , "changes" A..= steChanges traceEntry
            ]

instance A.FromJSON SchedulerTraceEntry where
    parseJSON =
        A.withObject "SchedulerTraceEntry" $ \o ->
            SchedulerTraceEntry
                <$> o .: "state_id"
                <*> o .: "changes"

instance A.ToJSON SchedulerArtifact where
    toJSON artifact =
        A.object
            [ "version" A..= saVersion artifact
            , "semantics" A..= saSemantics artifact
            , "objective" A..= saObjective artifact
            , "mdp_fingerprint" A..= saMDPFingerprint artifact
            , "states" A..= saStates artifact
            , "actions" A..= saActions artifact
            , "traces" A..= saTraces artifact
            ]

instance A.FromJSON SchedulerArtifact where
    parseJSON =
        A.withObject "SchedulerArtifact" $ \o -> do
            wrapped <- o .:? "scheduler"
            case wrapped of
                Just artifact -> pure artifact
                Nothing ->
                    SchedulerArtifact
                        <$> o .: "version"
                        <*> o .:? "semantics"
                        <*> o .: "objective"
                        <*> o .: "mdp_fingerprint"
                        <*> (o .:? "states" .!= [])
                        <*> (o .:? "actions" .!= [])
                        <*> o .: "traces"

instance RationalOrDouble p => A.ToJSON (CoverageStatus p) where
    toJSON status =
        case status of
            CoverageReached target budget value ->
                coverageToJSON "reached" target budget value
            CoverageUnreachable target budget value ->
                coverageToJSON "unreachable" target budget value
      where
        coverageToJSON :: RationalOrDouble p => String -> Double -> Int -> p -> A.Value
        coverageToJSON kind target budget value =
            A.object
                [ "status" A..= kind
                , "target" A..= target
                , "budget" A..= budget
                , "value" A..= toDouble value
                ]

instance (Ord s, Show s, IsList s, Show (Item s), RationalOrDouble p) => A.ToJSON (ExtremalResult s p) where
    toJSON result =
        let (cdfMin, cdfMax) = initialStateCDFSeries result
         in A.object
                [ "initial_state" A..= stateToJSON (erInitialState result)
                , "states" A..= fmap stateToJSON (erStates result)
                , "goal_states" A..= fmap stateToJSON (erGoalStates result)
                , "resolved_budget" A..= erResolvedBudget result
                , "mdp_fingerprint" A..= erMDPFingerprint result
                , "coverage_status" A..= erCoverageStatus result
                , "series" A..=
                    A.object
                        [ "cdf_min" A..= fmap toDouble cdfMin
                        , "cdf_max" A..= fmap toDouble cdfMax
                        ]
                , "schedulers" A..=
                    A.object
                        [ "min" A..= erMinScheduler result
                        , "max" A..= erMaxScheduler result
                        ]
                ]

instance (Ord s, Show s, IsList s, Show (Item s), RationalOrDouble p) => A.ToJSON (ScheduledResult s p) where
    toJSON = scheduledResultToJSON False

extremalDPTablesToJSON
    :: (Ord s, Show s, IsList s, Show (Item s), RationalOrDouble p)
    => ExtremalResult s p
    -> A.Value
extremalDPTablesToJSON result =
    A.object
        [ "columns" A..= initialStateTimeSeries result
        , "min" A..= tableRowsToJSON (erMinTable result)
        , "max" A..= tableRowsToJSON (erMaxTable result)
        ]
  where
    tableRowsToJSON table =
        [ A.object
            [ "state" A..= stateToJSON st
            , "values" A..= fmap toDouble (cdfRow table st (erResolvedBudget result))
            ]
        | st <- erStates result
        ]

stateToJSON :: (Show s, IsList s, Show (Item s)) => ConcreteMDPState s -> A.Value
stateToJSON st@(pc, bps) =
    A.object
        [ "pc" A..= pc
        , "bell_pairs" A..= fmap show (toList bps)
        , "rendered" A..= show st
        ]

scheduledResultToJSON
    :: (Ord s, Show s, IsList s, Show (Item s), RationalOrDouble p)
    => Bool
    -> ScheduledResult s p
    -> A.Value
scheduledResultToJSON includeOccupancy result =
    let base =
            A.object
                [ "initial_state" A..= stateToJSON (srInitialState result)
                , "states" A..= fmap stateToJSON (srStates result)
                , "goal_states" A..= fmap stateToJSON (srGoalStates result)
                , "resolved_budget" A..= srResolvedBudget result
                , "scheduler_horizon" A..= srSchedulerHorizon result
                , "mdp_fingerprint" A..= srMDPFingerprint result
                , "scheduler_objective" A..= srSchedulerObjective result
                , "series" A..=
                    A.object
                        [ "pmf" A..= fmap toDouble (scheduledPMFSeries result)
                        , "cdf" A..= fmap toDouble (scheduledCDFSeries result)
                        ]
                ]
     in if includeOccupancy
           then
                case base of
                    A.Object fields ->
                        A.Object $
                            AKM.insert "occupancy" (scheduledOccupancyToJSON result) fields
                    other -> other
           else base

scheduledOccupancyToJSON
    :: (Ord s, Show s, IsList s, Show (Item s), RationalOrDouble p)
    => ScheduledResult s p
    -> A.Value
scheduledOccupancyToJSON result =
    A.object
        [ "columns" A..= scheduledTimeSeries result
        , "active" A..=
            [ A.object
                [ "state" A..= stateToJSON st
                , "values" A..= fmap toDouble (occupancyRow values)
                ]
            | (st, values) <- Map.toList (srOccupancy result)
            ]
        ]
  where
    occupancyRow values =
        [ IM.findWithDefault 0 t values
        | t <- scheduledTimeSeries result
        ]

computeExtremalReachability
    :: (Ord s, Show s, IsList s, Show (Item s), RationalOrDouble p)
    => (s -> Bool)
    -> ExtremalQuery
    -> StateSystem (MDP p) s
    -> Either String (ExtremalResult s p)
computeExtremalReachability =
    computeExtremalReachabilityWithMetadata (SchedulerMetadata Nothing Nothing)

computeExtremalReachabilityWithMetadata
    :: (Ord s, Show s, IsList s, Show (Item s), RationalOrDouble p)
    => SchedulerMetadata
    -> (s -> Bool)
    -> ExtremalQuery
    -> StateSystem (MDP p) s
    -> Either String (ExtremalResult s p)
computeExtremalReachabilityWithMetadata metadata isGoal query ss = do
    validateExtremalQuery query
    let states = collectConcreteStates ss
        goalStates = filter (isGoal . snd) states
        goalSet = Set.fromList goalStates
        actions = buildActionMap ss states
        fingerprint = mdpFingerprint states actions

    validateNonNegativeCosts goalSet actions

    let (minTable, resolvedBudget, coverageStatus, minChoices) =
            computeExtremalTable selectMinAction query states goalSet actions (ssInitial ss)
        (maxTable, _, _, maxChoices) =
            computeExtremalTable selectMaxAction (ExtremalBudget resolvedBudget) states goalSet actions (ssInitial ss)
        minScheduler =
            buildSchedulerArtifact metadata SchedulerMin resolvedBudget fingerprint states actions minChoices
        maxScheduler =
            buildSchedulerArtifact metadata SchedulerMax resolvedBudget fingerprint states actions maxChoices

    pure $
        ExtremalResult
            { erInitialState = ssInitial ss
            , erStates = states
            , erGoalStates = goalStates
            , erResolvedBudget = resolvedBudget
            , erMDPFingerprint = fingerprint
            , erMinTable = minTable
            , erMaxTable = maxTable
            , erMinSchedulerChoices = minChoices
            , erMaxSchedulerChoices = maxChoices
            , erMinScheduler = minScheduler
            , erMaxScheduler = maxScheduler
            , erCoverageStatus = coverageStatus
            }

computeScheduledReachability
    :: (Ord s, Show s, RationalOrDouble p)
    => (s -> Bool)
    -> ExtremalQuery
    -> SchedulerArtifact
    -> StateSystem (MDP p) s
    -> Either String (ScheduledResult s p)
computeScheduledReachability isGoal query scheduler ss = do
    validateExtremalQuery query
    resolvedBudget <-
        case query of
            ExtremalBudget budget ->
                Right budget
            ExtremalCoverage{} ->
                Left "Injected scheduler solving requires --truncation, not --coverage."
    let states = collectConcreteStates ss
        goalStates = filter (isGoal . snd) states
        goalSet = Set.fromList goalStates
        actions = buildActionMap ss states
        fingerprint = mdpFingerprint states actions
    validateNonNegativeCosts goalSet actions
    traceMap <- validateSchedulerArtifact resolvedBudget fingerprint states actions scheduler
    let schedulerHorizon = soResolvedBudget (saObjective scheduler)
    replay <- computeScheduledReplay resolvedBudget schedulerHorizon (ssInitial ss) goalSet actions traceMap
    validateScheduledSeries (srepPMFSeries replay) (srepCDFSeries replay)
    pure $
        ScheduledResult
            { srInitialState = ssInitial ss
            , srStates = states
            , srGoalStates = goalStates
            , srResolvedBudget = resolvedBudget
            , srSchedulerHorizon = schedulerHorizon
            , srMDPFingerprint = fingerprint
            , srPMFSeries = srepPMFSeries replay
            , srCDFSeries = srepCDFSeries replay
            , srOccupancy = srepOccupancy replay
            , srSchedulerObjective = saObjective scheduler
            }

renderExtremalResult :: (Ord s, RationalOrDouble p, Show s) => ExtremalResult s p -> String
renderExtremalResult result =
    unlines $
        [ "Extremal cost-bounded reachability"
        , "Initial state: " <> show (erInitialState result)
        , "Goal states: " <> renderStateList (erGoalStates result)
        , "Computed up to budget: " <> show (erResolvedBudget result)
        ]
        <> maybe [] (\status -> [renderCoverageStatus status]) (erCoverageStatus result)
        <> [ ""
           , renderTable
                ["t", "pmf_min[t]", "pmf_max[t]", "cdf_min[t]", "cdf_max[t]"]
                [ [ show t
                  , show pmfMin
                  , show pmfMax
                  , show cdfMin
                  , show cdfMax
                  ]
                | (t, pmfMin, pmfMax, cdfMin, cdfMax) <- initialStateRows result
                ]
           , ""
           , renderSchedulerChoices "Worst scheduler choices (min CDF):" (erMinSchedulerChoices result)
           , renderSchedulerChoices "Best scheduler choices (max CDF):" (erMaxSchedulerChoices result)
           ]

renderExtremalDPTables :: (Ord s, RationalOrDouble p, Show s) => ExtremalResult s p -> String
renderExtremalDPTables result =
    unlines
        [ "DP table dump"
        , ""
        , renderDPTable "Min DP table:" (erMinTable result)
        , ""
        , renderDPTable "Max DP table:" (erMaxTable result)
        ]
  where
    renderDPTable title table =
        unlines $
            title :
            lines
                ( renderTable
                    ("state" : fmap (("t=" <>) . show) (initialStateTimeSeries result))
                    [ show st : fmap show (cdfRow table st (erResolvedBudget result))
                    | st <- erStates result
                    ]
                )

renderScheduledResult :: (Ord s, RationalOrDouble p, Show s) => ScheduledResult s p -> String
renderScheduledResult result =
    unlines
        [ "Scheduled cost-bounded reachability"
        , "Initial state: " <> show (srInitialState result)
        , "Goal states: " <> renderStateList (srGoalStates result)
        , "Computed up to budget: " <> show (srResolvedBudget result)
        , "Scheduler horizon: " <> show (srSchedulerHorizon result)
        , "Scheduler: "
            <> schedulerExtremumName (soExtremum (srSchedulerObjective result))
            <> maybe "" (" for " <>) (soEvent (srSchedulerObjective result))
        , ""
        , renderTable
            ["t", "pmf[t]", "cdf[t]"]
            [ [ show t
              , show pmf
              , show cdf
              ]
            | (t, pmf, cdf) <- scheduledRows result
            ]
        ]

renderStateList :: Show s => [ConcreteMDPState s] -> String
renderStateList [] = "none"
renderStateList xs = intercalate ", " (show <$> xs)

renderCoverageStatus :: Show p => CoverageStatus p -> String
renderCoverageStatus (CoverageReached target budget value) =
    "Coverage target " <> show target
        <> " reached for the worst scheduler at budget "
        <> show budget <> " with cdf_min[t] = " <> show value
renderCoverageStatus (CoverageUnreachable target budget value) =
    "Coverage target " <> show target
        <> " was not reached; the worst-scheduler CDF stabilised by budget "
        <> show budget <> " at cdf_min[t] = " <> show value

renderSchedulerChoices :: (Show s, RationalOrDouble p) => String -> [SchedulerChoiceTrace s p] -> String
renderSchedulerChoices title [] =
    unlines [title, "  none"]
renderSchedulerChoices title traces =
    unlines $ title : concatMap renderSchedulerChoiceTrace traces

renderSchedulerChoiceTrace :: (Show s, RationalOrDouble p) => SchedulerChoiceTrace s p -> [String]
renderSchedulerChoiceTrace trace =
    ("  state=" <> show (sctState trace))
        : fmap (("    " <>) . renderSchedulerChoice) (sctChanges trace)

renderSchedulerChoice :: (Show s, RationalOrDouble p) => SchedulerChoice s p -> String
renderSchedulerChoice choice =
    "from t=" <> show (scBudget choice)
        <> case scSelection choice of
            ChosenAction actionIndex value actionValues action ->
                ": choose action #" <> show actionIndex
                    <> " with value " <> formatSchedulerValue value
                    <> " as (" <> renderActionValues actionValues <> ")"
                    <> " -> " <> renderAction action
            AllActionsSameValue actionIndex value actionValues action ->
                ": choose tied action #" <> show actionIndex
                    <> " with value " <> formatSchedulerValue value
                    <> " as (" <> renderActionValues actionValues <> ")"
                    <> " -> " <> renderAction action

renderActionValues :: RationalOrDouble p => [(Int, p)] -> String
renderActionValues =
    intercalate ", " . fmap renderActionValue
  where
    renderActionValue (actionIndex, value) =
        "#" <> show actionIndex <> ": " <> formatSchedulerValue value

formatSchedulerValue :: RationalOrDouble p => p -> String
formatSchedulerValue value =
    showFFloat (Just 4) (toDouble value) ""

renderAction :: (Show s, Show p) => Action s p -> String
renderAction =
    intercalate "+" . fmap renderOutcome . D.toListD
  where
    renderOutcome ((nextState, cost), prob) =
        show nextState <> "×《" <> show prob <> ", " <> show cost <> "》"

renderTable :: [String] -> [[String]] -> String
renderTable headers rows =
    unlines $ renderRow widths headers : fmap (renderRow widths) rows
  where
    widths =
        fmap (maximum . fmap length) . transpose $ headers : rows

    renderRow ws cols =
        intercalate "  " $ zipWith padRight ws cols

    padRight width s = s <> replicate (max 0 (width - length s)) ' '

initialStateTimeSeries :: ExtremalResult s p -> [Int]
initialStateTimeSeries result = [0 .. erResolvedBudget result]

scheduledTimeSeries :: ScheduledResult s p -> [Int]
scheduledTimeSeries result = [0 .. srResolvedBudget result]

initialStateCDFSeries :: (Ord s, Num p) => ExtremalResult s p -> ([p], [p])
initialStateCDFSeries result =
    (cdfMin, cdfMax)
  where
    cdfMin = cdfRow (erMinTable result) (erInitialState result) (erResolvedBudget result)
    cdfMax = cdfRow (erMaxTable result) (erInitialState result) (erResolvedBudget result)

initialStatePMFSeries :: (Ord s, Num p) => ExtremalResult s p -> ([p], [p])
initialStatePMFSeries result =
    (pmfFromCDF cdfMin, pmfFromCDF cdfMax)
  where
    (cdfMin, cdfMax) = initialStateCDFSeries result

initialStateRows :: (Ord s, Num p) => ExtremalResult s p -> [(Int, p, p, p, p)]
initialStateRows result =
    zipWith5 rows ts pmfMin pmfMax cdfMin cdfMax
  where
    ts = initialStateTimeSeries result
    (cdfMin, cdfMax) = initialStateCDFSeries result
    (pmfMin, pmfMax) = initialStatePMFSeries result
    rows t pmfMin' pmfMax' cdfMin' cdfMax' = (t, pmfMin', pmfMax', cdfMin', cdfMax')

scheduledCDFSeries :: (Ord s, Num p) => ScheduledResult s p -> [p]
scheduledCDFSeries = srCDFSeries

scheduledPMFSeries :: (Ord s, Num p) => ScheduledResult s p -> [p]
scheduledPMFSeries = srPMFSeries

scheduledRows :: (Ord s, Num p) => ScheduledResult s p -> [(Int, p, p)]
scheduledRows result =
    zipWith3 rows (scheduledTimeSeries result) (scheduledPMFSeries result) (scheduledCDFSeries result)
  where
    rows t pmf cdf = (t, pmf, cdf)

cdfRow :: Ord s => Num p => ExtremalTable s p -> ConcreteMDPState s -> Int -> [p]
cdfRow table st budget =
    [ tableValue table st t
    | t <- [0 .. budget]
    ]

pmfFromCDF :: Num p => [p] -> [p]
pmfFromCDF [] = []
pmfFromCDF (x : xs) = x : zipWith (-) xs (x : xs)

collectConcreteStates :: Ord s => StateSystem (MDP p) s -> [ConcreteMDPState s]
collectConcreteStates ss =
    Set.toAscList $
        Set.singleton (ssInitial ss)
            <> Set.fromList
                [ (pc, bps)
                | (pc, perState) <- IM.toList (ssTransitions ss)
                , bps <- Map.keys perState
                ]
            <> Set.fromList
                [ next
                | (_, perState) <- IM.toList (ssTransitions ss)
                , (_, mdp) <- Map.toList perState
                , gen <- getGenerators (unMDP mdp)
                , ((next, _), _) <- D.toListD gen
                ]

buildActionMap
    :: Ord s
    => StateSystem (MDP p) s
    -> [ConcreteMDPState s]
    -> Map.Map (ConcreteMDPState s) [Action s p]
buildActionMap ss states =
    Map.fromList
        [ (st, actionGenerators st)
        | st <- states
        ]
  where
    actionGenerators (pc, bps) =
        maybe [] (getGenerators . unMDP) $
            IM.lookup pc (ssTransitions ss) >>= Map.lookup bps

validateExtremalQuery :: ExtremalQuery -> Either String ()
validateExtremalQuery (ExtremalBudget budget)
    | budget < 0 =
        Left "Extremal budget must be non-negative."
    | otherwise =
        Right ()
validateExtremalQuery (ExtremalCoverage target)
    | target < 0 || target > 1 =
        Left "Coverage must lie in the interval [0,1]."
    | otherwise =
        Right ()

validateNonNegativeCosts
    :: (Ord s, Show s)
    => Set.Set (ConcreteMDPState s)
    -> Map.Map (ConcreteMDPState s) [Action s p]
    -> Either String ()
validateNonNegativeCosts goalStates actions =
    case
        [ (st, cost)
        | (st, gens) <- Map.toList actions
        , st `Set.notMember` goalStates
        , gen <- gens
        , ((_, stepCost), _) <- D.toListD gen
        , let cost = getSum (getStepCost stepCost)
        , cost < 0
        ] of
        [] ->
            Right ()
        (st, cost) : _ ->
            Left $
                "The extremal DP solver requires non-negative step costs; "
                    <> "encountered cost " <> show cost <> " in state " <> show st

buildSchedulerArtifact
    :: (Ord s, Show s, IsList s, Show (Item s), RationalOrDouble p)
    => SchedulerMetadata
    -> SchedulerExtremum
    -> Int
    -> String
    -> [ConcreteMDPState s]
    -> Map.Map (ConcreteMDPState s) [Action s p]
    -> [SchedulerChoiceTrace s p]
    -> SchedulerArtifact
buildSchedulerArtifact metadata extremum resolvedBudget fingerprint states actions traces =
    SchedulerArtifact
        { saVersion = 1
        , saSemantics = smSemantics metadata
        , saObjective =
            SchedulerObjective
                { soEvent = smObjectiveEvent metadata
                , soExtremum = extremum
                , soResolvedBudget = resolvedBudget
                }
        , saMDPFingerprint = fingerprint
        , saStates = buildStateEntries states
        , saActions = buildActionEntries stateIds actions
        , saTraces = buildTraceEntries stateIds actions traces
        }
  where
    stateIds = stateIdMap states

buildStateEntries :: (Show s, IsList s, Show (Item s)) => [ConcreteMDPState s] -> [SchedulerStateEntry]
buildStateEntries states =
    [ SchedulerStateEntry
        { sseStateId = stateId
        , ssePC = pc
        , sseBellPairs = fmap show (toList bps)
        , sseRendered = show st
        }
    | (stateId, st@(pc, bps)) <- zip [0..] states
    ]

buildActionEntries
    :: (Ord s, Show s, Show p)
    => Map.Map (ConcreteMDPState s) Int
    -> Map.Map (ConcreteMDPState s) [Action s p]
    -> [SchedulerActionEntry]
buildActionEntries stateIds actions =
    [ SchedulerActionEntry
        { saeStateId = stateId
        , saeActionIndex = actionIndex
        , saeActionDigest = actionDigest stateIds action
        , saeRendered = renderAction action
        }
    | (st, gens) <- Map.toList actions
    , let stateId = lookupStateId stateIds st
    , (actionIndex, action) <- zip [1..] gens
    ]

buildTraceEntries
    :: (Ord s, Show s, RationalOrDouble p)
    => Map.Map (ConcreteMDPState s) Int
    -> Map.Map (ConcreteMDPState s) [Action s p]
    -> [SchedulerChoiceTrace s p]
    -> [SchedulerTraceEntry]
buildTraceEntries stateIds actions =
    fmap buildTraceEntry
  where
    buildTraceEntry trace =
        SchedulerTraceEntry
            { steStateId = lookupStateId stateIds (sctState trace)
            , steChanges = fmap (buildTraceChange (sctState trace)) (sctChanges trace)
            }

    buildTraceChange st choice =
        let selection = scSelection choice
            actionIndex = selectionActionIndex selection
            action =
                fromMaybe
                    (selectionAction selection)
                    (lookupActionByIndex actionIndex =<< Map.lookup st actions)
         in SchedulerTraceChange
                { stcFromBudget = scBudget choice
                , stcActionIndex = actionIndex
                , stcActionDigest = actionDigest stateIds action
                , stcTie = selectionTie selection
                , stcValue = Just . toDouble $ selectionValue selection
                }

stateIdMap :: Ord s => [ConcreteMDPState s] -> Map.Map (ConcreteMDPState s) Int
stateIdMap states = Map.fromList (zip states [0..])

lookupStateId :: (Ord s, Show s) => Map.Map (ConcreteMDPState s) Int -> ConcreteMDPState s -> Int
lookupStateId stateIds st =
    fromMaybe
        (error $ "lookupStateId: missing concrete state " <> show st)
        (Map.lookup st stateIds)

lookupActionByIndex :: Int -> [Action s p] -> Maybe (Action s p)
lookupActionByIndex actionIndex actions
    | actionIndex <= 0 = Nothing
    | otherwise =
        case drop (actionIndex - 1) actions of
            action:_ -> Just action
            [] -> Nothing

selectionActionIndex :: SchedulerSelection s p -> Int
selectionActionIndex (ChosenAction actionIndex _ _ _) = actionIndex
selectionActionIndex (AllActionsSameValue actionIndex _ _ _) = actionIndex

selectionValue :: SchedulerSelection s p -> p
selectionValue (ChosenAction _ value _ _) = value
selectionValue (AllActionsSameValue _ value _ _) = value

selectionTie :: SchedulerSelection s p -> Bool
selectionTie ChosenAction{} = False
selectionTie AllActionsSameValue{} = True

selectionAction :: SchedulerSelection s p -> Action s p
selectionAction (ChosenAction _ _ _ action) = action
selectionAction (AllActionsSameValue _ _ _ action) = action

mdpFingerprint
    :: (Ord s, Show s, Show p)
    => [ConcreteMDPState s]
    -> Map.Map (ConcreteMDPState s) [Action s p]
    -> String
mdpFingerprint states actions =
    stableDigest . unlines $
        [ "states" ]
        <> [ show stateId <> ":" <> show st
           | (stateId, st) <- zip [0 :: Int ..] states
           ]
        <> [ "actions" ]
        <> [ show (lookupStateId stateIds st)
                <> "#"
                <> show actionIndex
                <> ":"
                <> actionCanonical stateIds action
           | (st, gens) <- Map.toList actions
           , (actionIndex, action) <- zip [1 :: Int ..] gens
           ]
  where
    stateIds = stateIdMap states

actionDigest
    :: (Ord s, Show s, Show p)
    => Map.Map (ConcreteMDPState s) Int
    -> Action s p
    -> String
actionDigest stateIds =
    stableDigest . actionCanonical stateIds

actionCanonical
    :: (Ord s, Show s, Show p)
    => Map.Map (ConcreteMDPState s) Int
    -> Action s p
    -> String
actionCanonical stateIds =
    intercalate "+" . fmap renderOutcome . D.toListD
  where
    renderOutcome ((nextState, cost), prob) =
        show (lookupStateId stateIds nextState)
            <> "@"
            <> show (getSum (getStepCost cost))
            <> "@"
            <> show prob

stableDigest :: String -> String
stableDigest input =
    let hex = showHex (foldl' step fnvOffset input) ""
     in replicate (max 0 (16 - length hex)) '0' <> hex
  where
    fnvOffset :: Word64
    fnvOffset = 14695981039346656037

    fnvPrime :: Word64
    fnvPrime = 1099511628211

    step hash char =
        (hash `xor` fromIntegral (fromEnum char)) * fnvPrime

schedulerExtremumName :: SchedulerExtremum -> String
schedulerExtremumName SchedulerMin = "min"
schedulerExtremumName SchedulerMax = "max"

type InjectedScheduler s = Map.Map (ConcreteMDPState s) (IM.IntMap SchedulerTraceChange)

validateSchedulerArtifact
    :: (Ord s, Show s, Show p)
    => Int
    -> String
    -> [ConcreteMDPState s]
    -> Map.Map (ConcreteMDPState s) [Action s p]
    -> SchedulerArtifact
    -> Either String (InjectedScheduler s)
validateSchedulerArtifact requestedBudget fingerprint states actions scheduler = do
    if saVersion scheduler == 1
       then Right ()
       else Left $ "Unsupported scheduler artifact version " <> show (saVersion scheduler) <> "."
    if saMDPFingerprint scheduler == fingerprint
       then Right ()
       else Left $
            "Scheduler MDP fingerprint mismatch: artifact has "
                <> saMDPFingerprint scheduler
                <> ", current MDP has "
                <> fingerprint
                <> "."
    if requestedBudget <= soResolvedBudget (saObjective scheduler)
       then Right ()
       else Left $
            "Injected scheduler was solved only up to budget "
                <> show (soResolvedBudget (saObjective scheduler))
                <> ", but this run requested budget "
                <> show requestedBudget
                <> "."
    mapM_ validateActionEntry (saActions scheduler)
    fmap Map.fromList (mapM validateTraceEntry (saTraces scheduler))
  where
    statesById = IM.fromList (zip [0..] states)
    stateIds = stateIdMap states
    currentActionDigests =
        Map.fromList
            [ ((lookupStateId stateIds st, actionIndex), actionDigest stateIds action)
            | (st, gens) <- Map.toList actions
            , (actionIndex, action) <- zip [1..] gens
            ]

    validateActionEntry actionEntry =
        case Map.lookup (saeStateId actionEntry, saeActionIndex actionEntry) currentActionDigests of
            Just digest
                | digest == saeActionDigest actionEntry -> Right ()
                | otherwise ->
                    Left $
                        "Scheduler action digest mismatch for state_id="
                            <> show (saeStateId actionEntry)
                            <> ", action_index="
                            <> show (saeActionIndex actionEntry)
                            <> "."
            Nothing ->
                Left $
                    "Scheduler action catalog references unknown state/action: state_id="
                        <> show (saeStateId actionEntry)
                        <> ", action_index="
                        <> show (saeActionIndex actionEntry)
                        <> "."

    validateTraceEntry traceEntry = do
        st <-
            maybe
                (Left $ "Scheduler trace references unknown state_id=" <> show (steStateId traceEntry) <> ".")
                Right
                (IM.lookup (steStateId traceEntry) statesById)
        if strictlyIncreasing (fmap stcFromBudget (steChanges traceEntry))
           then Right ()
           else Left $ "Scheduler trace for state_id=" <> show (steStateId traceEntry) <> " has non-increasing budgets."
        changes <- mapM (validateTraceChange (steStateId traceEntry)) (steChanges traceEntry)
        Right (st, IM.fromList [(stcFromBudget change, change) | change <- changes])

    validateTraceChange stateId change = do
        if stcFromBudget change >= 0 && stcFromBudget change <= soResolvedBudget (saObjective scheduler)
           then Right ()
           else Left $
                "Scheduler trace has out-of-range budget "
                    <> show (stcFromBudget change)
                    <> " for state_id="
                    <> show stateId
                    <> "."
        case Map.lookup (stateId, stcActionIndex change) currentActionDigests of
            Just digest
                | digest == stcActionDigest change -> Right change
                | otherwise ->
                    Left $
                        "Scheduler trace action digest mismatch for state_id="
                            <> show stateId
                            <> ", action_index="
                            <> show (stcActionIndex change)
                            <> "."
            Nothing ->
                Left $
                    "Scheduler trace references unknown action_index="
                        <> show (stcActionIndex change)
                        <> " for state_id="
                        <> show stateId
                        <> "."

    strictlyIncreasing [] = True
    strictlyIncreasing [_] = True
    strictlyIncreasing (x:y:xs) = x < y && strictlyIncreasing (y:xs)

computeExtremalTable
    :: (Ord s, RationalOrDouble p)
    => ([(Int, Action s p, p)] -> (Int, Action s p, p))
    -> ExtremalQuery
    -> [ConcreteMDPState s]
    -> Set.Set (ConcreteMDPState s)
    -> Map.Map (ConcreteMDPState s) [Action s p]
    -> ConcreteMDPState s
    -> (ExtremalTable s p, Int, Maybe (CoverageStatus p), [SchedulerChoiceTrace s p])
computeExtremalTable selectAction query states goalStates actions initialState =
    go 0 0 initialTable Map.empty
  where
    initialTable =
        Map.fromList
            [ (st, IM.empty)
            | st <- states
            ]

    maxObservedCost =
        maximum $
            1 :
            [ getSum (getStepCost stepCost)
            | gens <- Map.elems actions
            , gen <- gens
            , ((_, stepCost), _) <- D.toListD gen
            ]

    go budget stableSteps table choices =
        let (table', budgetChoices) = appendBudget budget table
            choices' = recordSchedulerChoices choices budgetChoices
            stableSteps' =
                if budget > 0 && columnsApproxEqual table' budget (budget - 1)
                   then stableSteps + 1
                   else 0
            currentInitial = tableValue table' initialState budget
         in case query of
                ExtremalBudget maxBudget
                    | budget >= maxBudget -> (table', budget, Nothing, schedulerChoiceTraces choices')
                    | otherwise -> go (budget + 1) stableSteps' table' choices'
                ExtremalCoverage target
                    | meetsCoverage target currentInitial ->
                        ( table'
                        , budget
                        , Just (CoverageReached target budget currentInitial)
                        , schedulerChoiceTraces choices'
                        )
                    | stableSteps' >= maxObservedCost ->
                        ( table'
                        , budget
                        , Just (CoverageUnreachable target budget currentInitial)
                        , schedulerChoiceTraces choices'
                        )
                    | otherwise ->
                        go (budget + 1) stableSteps' table' choices'

    appendBudget budget table =
        let cells = foldl' (\memo st -> snd (resolveCell budget table memo st)) Map.empty states
            choicesForBudget = foldMap (maybe [] pure . bcChoice) (Map.elems cells)
            table' =
                foldl'
                    (\acc (st, cell) -> Map.adjust (IM.insert budget (bcValue cell)) st acc)
                    table
                    (Map.toList cells)
         in (table', choicesForBudget)

    -- TODO: zero-cost dependencies are assumed acyclic. Under that user-side
    -- precondition, recursive same-budget evaluation terminates; zero-cost
    -- loops should be rejected by a future validation pass.
    resolveCell budget table memo st
        | Just cell <- Map.lookup st memo = (cell, memo)
        | st `Set.member` goalStates =
            let cell = BudgetCell 1 Nothing
             in (cell, Map.insert st cell memo)
        | otherwise =
            case Map.findWithDefault [] st actions of
                [] ->
                    let cell = BudgetCell 0 Nothing
                     in (cell, Map.insert st cell memo)
                gens ->
                    let (memo', scoredActions) =
                            mapAccumL (scoreAction budget table) memo (zip [1..] gens)
                        (actionIndex, action, value) = selectAction scoredActions
                        actionValues =
                            [ (idx, actionValue)
                            | (idx, _, actionValue) <- scoredActions
                            ]
                        actionIsTied = selectedActionHasTie value scoredActions
                        choice =
                            if length gens > 1
                               then Just
                                    ( st
                                    , SchedulerChoice
                                        { scBudget = budget
                                        , scSelection =
                                            if actionIsTied
                                               then AllActionsSameValue actionIndex value actionValues action
                                               else ChosenAction actionIndex value actionValues action
                                        }
                                    )
                               else Nothing
                        cell = BudgetCell value choice
                     in (cell, Map.insert st cell memo')

    scoreAction budget table memo (actionIndex, action) =
        let (value, memo') =
                foldl'
                    (scoreOutcome budget table)
                    (0, memo)
                    (D.toListD action)
         in (memo', (actionIndex, action, value))

    scoreOutcome budget table (total, memo) ((nextState, cost), prob) =
        let costValue = getSum (getStepCost cost)
         in if costValue == 0
               then
                    let (cell, memo') = resolveCell budget table memo nextState
                     in (total + prob * bcValue cell, memo')
               else
                    ( total + prob * tableValue table nextState (budget - costValue)
                    , memo
                    )

computeScheduledReplay
    :: (Ord s, Show s, Num p)
    => Int
    -> Int
    -> ConcreteMDPState s
    -> Set.Set (ConcreteMDPState s)
    -> Map.Map (ConcreteMDPState s) [Action s p]
    -> InjectedScheduler s
    -> Either String (ScheduledReplay s p)
computeScheduledReplay requestedBudget schedulerHorizon initialState goalStates actions scheduler =
    go initialWork initialPMF Map.empty
  where
    initialIsGoal = initialState `Set.member` goalStates

    initialWork
        | initialIsGoal = IM.empty
        | otherwise = IM.singleton 0 (Map.singleton initialState 1)

    initialPMF
        | initialIsGoal = IM.singleton 0 1
        | otherwise = IM.empty

    go work pmf occupancy =
        case IM.minViewWithKey work of
            Nothing ->
                Right (buildReplay pmf occupancy)
            Just ((elapsed, activeAtElapsed), rest)
                | elapsed > requestedBudget ->
                    Right (buildReplay pmf occupancy)
                | otherwise -> do
                    (work', pmf', occupancy') <-
                        foldM
                            (advanceActive elapsed)
                            (rest, pmf, occupancy)
                            (Map.toList activeAtElapsed)
                    go work' pmf' occupancy'

    buildReplay pmf occupancy =
        let pmfSeries =
                [ IM.findWithDefault 0 t pmf
                | t <- [0 .. requestedBudget]
                ]
            cdfSeries = scanl1 (+) pmfSeries
         in ScheduledReplay
                { srepPMFSeries = pmfSeries
                , srepCDFSeries = cdfSeries
                , srepOccupancy = occupancy
                }

    advanceActive elapsed (work, pmf, occupancy) (st, mass)
        | st `Set.member` goalStates =
            Right (work, addPMF elapsed mass pmf, occupancy)
        | schedulerBudget <= 0 =
            Right (work, pmf, insertOccupancy elapsed st mass occupancy)
        | otherwise =
            case Map.findWithDefault [] st actions of
                [] ->
                    Right (work, pmf, insertOccupancy elapsed st mass occupancy)
                gens -> do
                    action <- selectInjectedAction scheduler schedulerBudget st gens
                    foldM
                        (advanceOutcome elapsed mass)
                        (work, pmf, insertOccupancy elapsed st mass occupancy)
                        (D.toListD action)
      where
        schedulerBudget = schedulerHorizon - elapsed

    advanceOutcome elapsed mass (work, pmf, occupancy) ((nextState, cost), prob) =
        let costValue = getSum (getStepCost cost)
            arrivalTime = elapsed + costValue
            nextMass = mass * prob
         in if costValue > schedulerHorizon - elapsed || arrivalTime > requestedBudget
               then Right (work, pmf, occupancy)
               else
                    Right $
                        if nextState `Set.member` goalStates
                           then (work, addPMF arrivalTime nextMass pmf, occupancy)
                           else (addWork arrivalTime nextState nextMass work, pmf, occupancy)

    addWork elapsed st mass =
        IM.insertWith
            (Map.unionWith (+))
            elapsed
            (Map.singleton st mass)

    addPMF elapsed mass =
        IM.insertWith (+) elapsed mass

    insertOccupancy elapsed st mass =
        Map.insertWith
            (IM.unionWith (+))
            st
            (IM.singleton elapsed mass)

selectInjectedAction
    :: (Ord s, Show s)
    => InjectedScheduler s
    -> Int
    -> ConcreteMDPState s
    -> [Action s p]
    -> Either String (Action s p)
selectInjectedAction _ _ _ [action] =
    Right action
selectInjectedAction scheduler schedulerBudget st gens =
    case Map.lookup st scheduler >>= IM.lookupLE schedulerBudget of
        Nothing ->
            Left $
                "Injected scheduler has no choice for state "
                    <> show st
                    <> " at scheduler budget "
                    <> show schedulerBudget
                    <> "."
        Just (_, change) ->
            maybe
                ( Left $
                    "Injected scheduler chose missing action_index="
                        <> show (stcActionIndex change)
                        <> " for state "
                        <> show st
                        <> "."
                )
                Right
                (lookupActionByIndex (stcActionIndex change) gens)

validateScheduledSeries :: RationalOrDouble p => [p] -> [p] -> Either String ()
validateScheduledSeries pmf cdf =
    case negativePMF of
        (t, value):_ ->
            Left $
                "Scheduled replay produced negative PMF at t="
                    <> show t
                    <> ": "
                    <> show (toDouble value)
                    <> "."
        [] ->
            case nonMonotoneCDF of
                (t, prevValue, value):_ ->
                    Left $
                        "Scheduled replay produced non-monotone CDF at t="
                            <> show t
                            <> ": previous="
                            <> show (toDouble prevValue)
                            <> ", current="
                            <> show (toDouble value)
                            <> "."
                [] ->
                    case outOfRangeCDF of
                        (t, value):_ ->
                            Left $
                                "Scheduled replay produced CDF outside [0,1] at t="
                                    <> show t
                                    <> ": "
                                    <> show (toDouble value)
                                    <> "."
                        [] -> Right ()
  where
    tolerance = 1e-12 :: Double
    negativePMF =
        [ (t, value)
        | (t, value) <- zip [0 :: Int ..] pmf
        , toDouble value < negate tolerance
        ]
    nonMonotoneCDF =
        [ (t, prevValue, value)
        | (t, prevValue, value) <- zip3 [1 :: Int ..] cdf (drop 1 cdf)
        , toDouble value + tolerance < toDouble prevValue
        ]
    outOfRangeCDF =
        [ (t, value)
        | (t, value) <- zip [0 :: Int ..] cdf
        , toDouble value < negate tolerance || toDouble value > 1 + tolerance
        ]

recordSchedulerChoices
    :: Ord s
    => SchedulerChoiceLog s p
    -> [(ConcreteMDPState s, SchedulerChoice s p)]
    -> SchedulerChoiceLog s p
recordSchedulerChoices =
    foldl' recordChoice
  where
    recordChoice logByState (st, choice) =
        Map.alter (Just . appendIfChanged choice) st logByState

    appendIfChanged choice Nothing = [choice]
    appendIfChanged choice (Just []) = [choice]
    appendIfChanged choice (Just existing@(latest:_))
        | sameScheduledAction (scSelection latest) (scSelection choice) = existing
        | otherwise = choice : existing

sameScheduledAction :: SchedulerSelection s p -> SchedulerSelection s p -> Bool
sameScheduledAction left right =
    selectionActionIndex left == selectionActionIndex right
        && selectionTie left == selectionTie right

schedulerChoiceTraces :: SchedulerChoiceLog s p -> [SchedulerChoiceTrace s p]
schedulerChoiceTraces =
    fmap toTrace . Map.toList
  where
    toTrace (st, choices) =
        SchedulerChoiceTrace
            { sctState = st
            , sctChanges = reverse choices
            }

selectMinAction :: RationalOrDouble p => [(Int, a, p)] -> (Int, a, p)
selectMinAction = selectActionBy (<)

selectMaxAction :: RationalOrDouble p => [(Int, a, p)] -> (Int, a, p)
selectMaxAction = selectActionBy (>)

selectedActionHasTie :: RationalOrDouble p => p -> [(Int, a, p)] -> Bool
selectedActionHasTie selectedValue =
    (> 1) . length . filter (\(_, _, value) -> approxEqual selectedValue value)

selectActionBy :: RationalOrDouble p => (p -> p -> Bool) -> [(Int, a, p)] -> (Int, a, p)
selectActionBy _ [] =
    error "selectActionBy: empty action list"
selectActionBy better (x:xs) =
    foldl' choose x xs
  where
    choose best@(_, _, bestValue) candidate@(_, _, candidateValue)
        | approxEqual candidateValue bestValue = best
        | candidateValue `better` bestValue = candidate
        | otherwise = best

tableValue :: Ord s => Num p => ExtremalTable s p -> ConcreteMDPState s -> Int -> p
tableValue _ _ budget | budget < 0 = 0
tableValue table st budget =
    fromMaybe 0 $
        Map.lookup st table >>= IM.lookup budget

columnsApproxEqual :: (Ord s, RationalOrDouble p) => ExtremalTable s p -> Int -> Int -> Bool
columnsApproxEqual table left right =
    all approxEntry (Map.keys table)
  where
    approxEntry st = approxEqual (tableValue table st left) (tableValue table st right)

approxEqual :: RationalOrDouble p => p -> p -> Bool
approxEqual x y = abs (toDouble (x - y)) <= 1e-12

meetsCoverage :: RationalOrDouble p => Double -> p -> Bool
meetsCoverage target value = toDouble value >= target
