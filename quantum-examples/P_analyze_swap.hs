import BellKAT.QuantumEventParser
import BellKAT.QuantumPrelude
import System.Environment (getArgs, withArgs)


pGen :: QBKATPolicy
pGen =
            ite ("A" /~? "B" &&* "A" /~? "C") (ucreate ("A", "B")) mempty
        <||>
            ite ("B" /~? "C" &&* "B" /~? "D" &&* "A" /~? "C") (ucreate ("B", "C")) mempty
        <||>
            ite ("C" /~? "D" &&* "B" /~? "D") (ucreate ("C", "D")) mempty


pOpt :: QBKATPolicy
pOpt = while ("A" /~? "D")
    (
        pGen
        <||>
            sswap ["B", "C"] ("A", "D")
        <||>
            swap "B" ("A", "D")
        <||>
            swap "C" ("A", "D")
        <||>
            swap "B" ("A", "C")
        <||>
            idle [("A", "B"), ("B", "C")]
        <||>
            swap "C" ("B", "D")
        <||>
            idle [("B", "C"), ("C", "D")]
    )


networkCapacity :: NetworkCapacity QBKATTag
networkCapacity = ["A" ~ "B", "B" ~ "C", "C" ~ "D", "A" ~ "C", "B" ~ "D", "A" ~ "D"]

nb :: NetworkBounds QBKATTag
nb = def
    { nbCapacity = Just networkCapacity
    , nbOperationTiming = InstantaneousOps
    }

-- Case 2: heterogeneous, sequential is better cause it fixes the optimal swap (start from the right swap, then the left one)
actionConfig :: Double -> Int -> ProbabilisticActionConfiguration
actionConfig w0 tCoh = PAC
    { pacTransmitProbability = []
    , pacCreateProbability = []
    , pacCreateWerner = []
    , pacUCreateProbability = [(("A", "B"), 1/2), (("B", "C"), 1/2), (("C", "D"), 1/20)]
    , pacUCreateWerner = [(("A", "B"), w0), (("B", "C"), w0), (("C", "D"), w0)]
    , pacSwapProbability = [("B", 1/2), ("C", 1/2)]
    , pacCoherenceTime = [("A", tCoh), ("B", tCoh), ("C", tCoh), ("D", tCoh)]
    , pacDistances =
    [ (("A", "B"), 1)
    , (("B", "C"), 1)
    , (("C", "D"), 1)
    , (("A", "C"), 2)
    , (("B", "D"), 2)
    , (("A", "D"), 3)
    ]
    }

main :: IO ()
main = do
    args <- getArgs
    exampleArgs <- either fail pure (stripEventArgs "A~D" args)
    ev <- either fail pure (parseQBKATEventExpr (eaEventExpr exampleArgs))
    let w0 = 958/1000
        tCoh = 1000
    withArgs (eaQbkatArgs exampleArgs) $
        qbkatMainD (actionConfig w0 tCoh) nb ev pOpt mempty
